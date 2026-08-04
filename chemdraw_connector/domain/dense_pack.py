"""Dense irregular-shape packing of CDXML structures onto one page.

Built for generating a structure-field background (hundreds to thousands of
molecules tiled edge-to-edge on a single canvas), but the same problem shows
up any time many structures need to be laid out compactly: an SI compound
grid, a poster panel, a scope-table page that's outgrown its box.

This is the "irregular/nesting packing" problem (garment cutting, sheet-metal
nesting, PCB placement all use variants of it). The textbook approach is
No-Fit-Polygon: compute the exact contact geometry between two arbitrary
outlines so one can slide directly against the other. That's expensive for
concave organic shapes with dozens of atoms. What's here instead is a raster
approximation of the same idea: each structure's bonds/atoms are stamped
into a boolean occupancy grid, and testing "does shape A fit at every
possible offset on the page" is one 2D FFT cross-correlation (free_positions)
rather than one geometry check per candidate offset. Cheap enough to run a
full rotation/mirror search per placement and a compaction sweep afterward,
both of which a naive per-offset scan would make impractically slow.

Pipeline: read_fragments -> largest_component + usable (quality gate) ->
struct_key (dedup) -> normalise (uniform bond length) -> pack or compact_fit
(initial placement) -> compact_sweep (close residual gaps) -> emit_page.

Zero COM imports; pure XML + numpy + RDKit (already a project dependency,
used here only for canonical-SMILES dedup — no chemistry is asserted or
changed).
"""
import hashlib
import math
import random
import statistics
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

import numpy as np
from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")

NICK_TYPES = {"Nickname", "Fragment", "GenericNickname", "AnonymousAlternativeGroup"}
_ORDER_TO_RDKIT = {
    1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE,
    3: Chem.BondType.TRIPLE, 4: Chem.BondType.QUADRUPLE,
    15: Chem.BondType.AROMATIC,
}
_ORDER_STR_TO_INT = {"1": 1, "2": 2, "3": 3, "4": 4, "1.5": 15}


# ------------------------------------------------------------------ reading
def read_fragments(cdxml_text):
    """Top-level <fragment> elements -> [{"nodes": [...], "bonds": [...]}].

    Deliberately separate from domain/cdxml_graph.py's parser: that one
    drops position (it's built for substructure matching, where geometry is
    irrelevant) and this module's entire job is geometry. Node/bond field
    names match cdxml_graph.py's convention where they overlap so the two
    aren't a source of confusion side by side.

    A nickname/generic-group node (NodeType in NICK_TYPES) is kept as ONE
    vertex carrying its label text, matching cdxml_graph.py's "contracted
    label is a dummy atom" rule -- the caller decides whether to expand
    those first (e.g. via a live chemdraw_expand_labels pass) or pack them
    as-is.
    """
    root = ET.fromstring(cdxml_text) if isinstance(cdxml_text, str) else cdxml_text
    parent = {c: p for p in root.iter() for c in p}
    out = []
    for f in root.iter("fragment"):
        p, nested = parent.get(f), False
        while p is not None:
            if p.tag in ("n", "fragment"):
                nested = True
                break
            p = parent.get(p)
        if nested:
            continue
        nodes, bonds = [], []
        for n in f.findall("n"):
            pos = None
            if n.get("p"):
                try:
                    x, y = (float(v) for v in n.get("p").split()[:2])
                    pos = (x, y)
                except ValueError:
                    pass
            t = n.find("t")
            label = "".join((s.text or "") for s in t.findall("s")).strip() if t is not None else ""
            node_type = n.get("NodeType", "Element")
            try:
                element = int(n.get("Element", "6"))
            except ValueError:
                element = 6
            try:
                charge = int(float(n.get("Charge", "0")))
            except ValueError:
                charge = 0
            nodes.append({
                "id": n.get("id"), "element": element, "charge": charge,
                "node_type": node_type, "label": label, "pos": pos,
            })
        for b in f.findall("b"):
            order_attr = b.get("Order", "1")
            order = order_attr if isinstance(order_attr, int) else _ORDER_STR_TO_INT.get(order_attr, 1)
            bonds.append({
                "begin": b.get("B"), "end": b.get("E"),
                "order": order, "display": b.get("Display"),
            })
        if nodes:
            out.append({"nodes": nodes, "bonds": bonds})
    return out


def largest_component(fr):
    """Keep only the biggest connected piece -- a stray counter-ion or a
    disconnected label sitting in the same <fragment> shouldn't drag the
    real molecule's bounding box around."""
    adj = defaultdict(set)
    for b in fr["bonds"]:
        adj[b["begin"]].add(b["end"])
        adj[b["end"]].add(b["begin"])
    seen, comps = set(), []
    for n in fr["nodes"]:
        nid = n["id"]
        if nid in seen:
            continue
        stack, comp = [nid], []
        seen.add(nid)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nb in adj.get(cur, ()):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append(comp)
    if len(comps) <= 1:
        return fr
    keep = set(max(comps, key=len))
    return {
        "nodes": [n for n in fr["nodes"] if n["id"] in keep],
        "bonds": [b for b in fr["bonds"] if b["begin"] in keep and b["end"] in keep],
    }


# ------------------------------------------------------------------ quality gate
def bond_lengths(fr):
    pos = {n["id"]: n["pos"] for n in fr["nodes"] if n.get("pos")}
    out = []
    for b in fr["bonds"]:
        a, c = pos.get(b["begin"]), pos.get(b["end"])
        if a and c:
            v = math.hypot(a[0] - c[0], a[1] - c[1])
            if v > 1e-6:
                out.append(v)
    return out


def geometry_defect(fr, tol=0.30):
    """Fraction of bonds more than `tol` off the structure's own median
    bond length -- the "how mangled is this drawing" signal.

    Source structures pooled from years of hand-drawn/copy-pasted content
    include plenty squashed or stretched to fit a slide; those render as
    crossing, overlapping rings even though connectivity is fine. Counting
    HOW MANY bonds are off the median (not max/min) ignores the one bond
    legitimately shortened to make room for a label, while still catching
    genuinely mangled structures. Confirmed against a 700-structure pool:
    population median sits near 0.09; a structure scoring 0.50 renders
    visibly broken.
    """
    lens = bond_lengths(fr)
    if len(lens) < 2:
        return 1.0
    med = statistics.median(lens)
    return sum(1 for v in lens if abs(v - med) / med > tol) / len(lens)


def usable(fr, min_atoms=10, max_atoms=55, max_defect=0.18, max_aspect=4.0):
    """The packing quality gate: enough atoms to read as a real structure,
    not so many it dominates the page, drawn cleanly, and not a long thin
    sliver (a lipid tail or alkyl chain) that would streak across many
    neighbours and break the even, tiled look."""
    n = len(fr["nodes"])
    if n < min_atoms or n > max_atoms:
        return False
    placed = [x for x in fr["nodes"] if x.get("pos")]
    if len(placed) < n * 0.9:
        return False
    lens = bond_lengths(fr)
    if len(lens) < max(3, n // 4):
        return False
    if geometry_defect(fr) > max_defect:
        return False
    xs = [x["pos"][0] for x in placed]
    ys = [x["pos"][1] for x in placed]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if max(w, h) <= 0:
        return False
    if max(w, h) / max(min(w, h), 1e-6) > max_aspect:
        return False
    return True


# ------------------------------------------------------------------ dedup
def _order_int(o):
    return _ORDER_STR_TO_INT.get(o, 1) if isinstance(o, str) else (o or 1)


def _to_rdkit_mol(fr):
    """Nickname/generic-group nodes (unexpanded shorthand) become dummy
    atoms keyed by their label text's hash, so two drawings sharing the
    same unexpanded label (e.g. both drawn with a plain "Boc" nickname)
    still compare equal without needing real atoms for it.

    Deliberately separate from domain/dedup.py, which dedups already-LIVE
    ChemDraw structures by InChIKey computed over COM. This runs on raw
    parsed fragment data before anything is opened in ChemDraw at all (the
    whole point of this module is to pack structures without paying for a
    COM round trip per structure), so it needs its own offline chemistry
    build -- RDKit canonical SMILES is the cheapest reliable equivalent.
    """
    m = Chem.RWMol()
    idx = {}
    for n in fr["nodes"]:
        if n["node_type"] in NICK_TYPES:
            a = Chem.Atom(0)
            lab = n.get("label") or "R"
            a.SetIsotope(1 + int(hashlib.md5(lab.encode()).hexdigest(), 16) % 400)
        else:
            el = n["element"] if n["element"] and n["element"] > 0 else 6
            try:
                a = Chem.Atom(int(el))
            except Exception:
                a = Chem.Atom(6)
        a.SetFormalCharge(int(n.get("charge") or 0))
        idx[n["id"]] = m.AddAtom(a)
    for b in fr["bonds"]:
        i, j = idx.get(b["begin"]), idx.get(b["end"])
        if i is None or j is None or i == j or m.GetBondBetweenAtoms(i, j):
            continue
        m.AddBond(i, j, _ORDER_TO_RDKIT.get(_order_int(b["order"]), Chem.BondType.SINGLE))
    mol = m.GetMol()
    Chem.SanitizeMol(
        mol,
        Chem.SanitizeFlags.SANITIZE_SYMMRINGS
        | Chem.SanitizeFlags.SANITIZE_SETCONJUGATION
        | Chem.SanitizeFlags.SANITIZE_ADJUSTHS,
        catchErrors=True,
    )
    return mol


def struct_key(fr):
    """Canonical SMILES: catches duplicate drawings of the same compound
    even when one copy is aromatic-perceived and the other Kekulized, which
    a plain element/degree fingerprint would miss. Falls back to that
    coarser fingerprint only if RDKit can't make sense of the fragment at
    all (a genuinely disconnected drawing that slipped past
    largest_component, or similar)."""
    try:
        mol = _to_rdkit_mol(fr)
        if mol.GetNumAtoms() > 0:
            smi = Chem.MolToSmiles(mol, canonical=True)
            if smi:
                return ("smiles", smi)
    except Exception:
        pass
    deg = Counter()
    for b in fr["bonds"]:
        deg[b["begin"]] += 1
        deg[b["end"]] += 1
    sig = sorted((n["element"], deg.get(n["id"], 0)) for n in fr["nodes"])
    orders = sorted(_order_int(b["order"]) for b in fr["bonds"])
    return ("fallback", tuple(sig), tuple(orders))


def dedup_pool(fragments, min_atoms=10, max_atoms=55, max_defect=0.18, max_aspect=4.0):
    """Run largest_component + usable + struct_key over a raw fragment
    list in one pass -- the normal way to turn read_fragments' output into
    a pack()-ready pool. Returns fragments in first-seen order."""
    pool, seen = [], set()
    for fr in fragments:
        fr = largest_component(fr)
        if not usable(fr, min_atoms, max_atoms, max_defect, max_aspect):
            continue
        k = struct_key(fr)
        if k in seen:
            continue
        seen.add(k)
        pool.append(fr)
    return pool


# ------------------------------------------------------------------ geometry
def normalise(fr, target_bond):
    """Scale to a uniform bond length and centre on the origin -- what
    makes a packed field read as one even texture instead of a jumble of
    different drawing scales."""
    lens = bond_lengths(fr)
    s = target_bond / statistics.median(lens)
    pts = {n["id"]: (n["pos"][0] * s, n["pos"][1] * s) for n in fr["nodes"] if n.get("pos")}
    xs = [p[0] for p in pts.values()]
    ys = [p[1] for p in pts.values()]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return {
        "nodes": [
            dict(n, pos=(pts[n["id"]][0] - cx, pts[n["id"]][1] - cy) if n["id"] in pts else None)
            for n in fr["nodes"]
        ],
        "bonds": fr["bonds"],
    }


def oriented(fr, angle, mirror):
    ca, sa = math.cos(angle), math.sin(angle)
    nodes = []
    for n in fr["nodes"]:
        if not n.get("pos"):
            nodes.append(n)
            continue
        x, y = n["pos"]
        if mirror:
            x = -x
        nodes.append(dict(n, pos=(x * ca - y * sa, x * sa + y * ca)))
    return {"nodes": nodes, "bonds": fr["bonds"]}


def stamp(fr, cell, pad_cells):
    """Rasterise one structure's bonds+atoms into a boolean mask.

    Returns (mask, ox, oy): mask[row, col] with the structure's min corner
    at (ox, oy) in page units. Heteroatom/labelled positions are stamped
    fatter than a bare bond line, since a drawn label occupies more room
    than the line alone. pad_cells dilates the whole mask by that many
    cells, which is how enforced whitespace between neighbouring
    structures gets implemented -- pad_cells=0 lets structures sit flush
    against each other.
    """
    pts = {n["id"]: n["pos"] for n in fr["nodes"] if n.get("pos")}
    xs = [p[0] for p in pts.values()]
    ys = [p[1] for p in pts.values()]
    ox, oy = min(xs), min(ys)
    w = int(math.ceil((max(xs) - ox) / cell)) + 1 + 2 * pad_cells
    h = int(math.ceil((max(ys) - oy) / cell)) + 1 + 2 * pad_cells
    m = np.zeros((h, w), dtype=bool)

    def put(px, py):
        c = int((px - ox) / cell) + pad_cells
        r = int((py - oy) / cell) + pad_cells
        if 0 <= r < h and 0 <= c < w:
            m[r, c] = True

    for b in fr["bonds"]:
        a, c = pts.get(b["begin"]), pts.get(b["end"])
        if not a or not c:
            continue
        steps = max(2, int(math.hypot(a[0] - c[0], a[1] - c[1]) / (cell * 0.5)))
        for i in range(steps + 1):
            t = i / steps
            put(a[0] + (c[0] - a[0]) * t, a[1] + (c[1] - a[1]) * t)
    for n in fr["nodes"]:
        if not n.get("pos"):
            continue
        labelled = n["node_type"] in NICK_TYPES or n["element"] != 6
        r = 2 if labelled else 1
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                put(n["pos"][0] + dx * cell, n["pos"][1] + dy * cell)

    if pad_cells:
        d = m.copy()
        for _ in range(pad_cells):
            g = d.copy()
            g[1:, :] |= d[:-1, :]
            g[:-1, :] |= d[1:, :]
            g[:, 1:] |= d[:, :-1]
            g[:, :-1] |= d[:, 1:]
            d = g
        m = d
    return m, ox - pad_cells * cell, oy - pad_cells * cell


# ------------------------------------------------------------------ collision
def free_positions(occ_f, mask, H, W):
    """Every top-left offset (r, c) where mask fits on occ without
    touching existing ink.

    Overlap at every one of the ~H*W candidate offsets is answered by ONE
    2D cross-correlation (mask against the occupancy grid's rfft2), not a
    per-offset scan -- the difference between this staying interactive at
    thousands of structures and not.
    """
    mh, mw = mask.shape
    if mh > H or mw > W:
        return None
    mf = np.fft.rfft2(mask[::-1, ::-1].astype(np.float64), s=(H, W))
    corr = np.fft.irfft2(occ_f * mf, s=(H, W))
    valid = corr[mh - 1:H, mw - 1:W]
    return valid < 0.5  # FFT round-off: a true zero is never >= 0.5


_ANGLES_DEFAULT = (0, 60, 120, 180, 240, 300)


# ------------------------------------------------------------------ packing
def pack(pool, page_w, page_h, cell, pad_cells, seed=7, max_units=4000,
         angles=_ANGLES_DEFAULT, orientations_per_attempt=True):
    """First-fit-decreasing onto a FIXED-size page, REPEATS allowed: large
    structures first, small ones filling the gaps, cycling back through
    the pool until the page is full or max_units is hit.

    orientations_per_attempt=True tries every (angle, mirror) combination
    for each placement and keeps the topmost-then-leftmost fit among all
    of them, instead of committing to one random orientation per attempt.
    This is the difference between "does A fit here" and "does the BEST
    orientation of A fit here" -- meaningfully denser (confirmed: +2.6%
    more structures in the same page on a 1300+ structure field), at the
    cost of len(angles)*2 collision tests per placement instead of 1. Set
    False to fall back to the cheap single-random-orientation version for
    very large pools where that cost matters more than the density gain.
    """
    rng = random.Random(seed)
    W = int(page_w / cell)
    H = int(page_h / cell)
    occ = np.zeros((H, W), dtype=bool)
    placed = []
    order = sorted(range(len(pool)), key=lambda i: -len(pool[i]["nodes"]))
    dirty = True
    occ_f = np.fft.rfft2(occ.astype(np.float64), s=(H, W))

    while dirty and len(placed) < max_units:
        dirty = False
        for i in order:
            if len(placed) >= max_units:
                break
            best = None
            candidates = (
                [(a, mr) for a in angles for mr in (False, True)]
                if orientations_per_attempt
                else [(rng.choice(angles), rng.random() < 0.5)]
            )
            for ang, mirror in candidates:
                shaped = oriented(pool[i], math.radians(ang), mirror)
                m, ox, oy = stamp(shaped, cell, pad_cells)
                ok = free_positions(occ_f, m, H, W)
                if ok is None or not ok.any():
                    continue
                rows = np.flatnonzero(ok.any(axis=1))
                r = int(rows[0])
                c = int(np.flatnonzero(ok[r])[0])
                score = r * W + c
                if best is None or score < best[0]:
                    best = (score, r, c, shaped, ox, oy, m)
            if best is None:
                continue
            _, r, c, shaped, ox, oy, m = best
            mh, mw = m.shape
            occ[r:r + mh, c:c + mw] |= m
            occ_f = np.fft.rfft2(occ.astype(np.float64), s=(H, W))
            placed.append({"placed": shaped, "dx": c * cell - ox, "dy": r * cell - oy})
            dirty = True
    return placed, occ, cell


def compact_fit(pool, cell, pad_cells, aspect, seed=7, angles=_ANGLES_DEFAULT):
    """Shrink-to-fit compaction, NO repeats: each unique structure placed
    at most once, on the smallest aspect-ratio-matched canvas that still
    fits all of them. Grows a low-ball area estimate geometrically until
    everything fits, then bisects to tighten it. Use this instead of
    pack() when the caller wants every structure exactly once (e.g. a
    "one of each" reference sheet) rather than a fully tiled field.
    """
    rng = random.Random(seed)

    def pack_once(page_w, page_h):
        W, H = int(page_w / cell), int(page_h / cell)
        occ = np.zeros((H, W), dtype=bool)
        occ_f = np.fft.rfft2(occ.astype(np.float64), s=(H, W))
        placed, unplaced = [], []
        order = sorted(range(len(pool)), key=lambda i: -len(pool[i]["nodes"]))
        for i in order:
            best = None
            for ang in angles:
                for mirror in (False, True):
                    shaped = oriented(pool[i], math.radians(ang), mirror)
                    m, ox, oy = stamp(shaped, cell, pad_cells)
                    ok = free_positions(occ_f, m, H, W)
                    if ok is None or not ok.any():
                        continue
                    rows = np.flatnonzero(ok.any(axis=1))
                    r = int(rows[0])
                    c = int(np.flatnonzero(ok[r])[0])
                    score = r * W + c
                    if best is None or score < best[0]:
                        best = (score, r, c, shaped, ox, oy, m)
            if best is None:
                unplaced.append(i)
                continue
            _, r, c, shaped, ox, oy, m = best
            mh, mw = m.shape
            occ[r:r + mh, c:c + mw] |= m
            occ_f = np.fft.rfft2(occ.astype(np.float64), s=(H, W))
            placed.append({"placed": shaped, "dx": c * cell - ox, "dy": r * cell - oy})
        return placed, occ, unplaced

    total_cells = sum(stamp(fr, cell, pad_cells)[0].sum() for fr in pool)
    area = total_cells / 0.45  # conservative packing-efficiency floor
    h = math.sqrt(area / aspect)
    w = h * aspect

    scale = 1.0
    for _ in range(20):
        placed, occ, unplaced = pack_once(w * scale, h * scale)
        if not unplaced:
            break
        scale *= 1.15
    else:
        raise RuntimeError("could not fit every structure even at 20x the area estimate")

    lo, hi = scale / 1.15, scale
    for _ in range(6):
        mid = (lo + hi) / 2
        p2, o2, u2 = pack_once(w * mid, h * mid)
        if u2:
            lo = mid
        else:
            hi = mid
            placed, occ = p2, o2
    return placed, occ, w * hi, h * hi, cell


def compact_sweep(placed, occ, cell, pad_cells, H, W, max_sweeps=3, rng_seed=7):
    """Gravity-style compaction post-pass: repeatedly pull each placed
    piece out of the grid and re-insert it at the current best
    (topmost-then-leftmost) open spot against everyone ELSE's now-settled
    position, largest pieces first each sweep. Converges when a full sweep
    moves nothing, or after max_sweeps.

    Why this is a separate pass rather than folded into pack()/compact_fit:
    those place pieces ONE AT A TIME against whatever's already down, so an
    early placement can block a better arrangement that only becomes
    visible once later (smaller) pieces have filled in around it. Freeing
    a piece and re-offering it the same global search after the page has
    filled in lets it slide into gaps that didn't exist yet when it was
    first placed -- the standard "shake"/re-insertion compaction technique
    used in production nesting software, adapted to this module's raster
    collision test instead of exact polygon geometry.
    """
    rng = random.Random(rng_seed)
    order_template = sorted(range(len(placed)), key=lambda i: -stamp(placed[i]["placed"], cell, pad_cells)[0].sum())

    occ_f = np.fft.rfft2(occ.astype(np.float64), s=(H, W))
    for _sweep in range(max_sweeps):
        changed = False
        for i in order_template:
            p = placed[i]
            m, ox, oy = stamp(p["placed"], cell, pad_cells)
            mh, mw = m.shape
            r0 = int(round((p["dy"] + oy) / cell))
            c0 = int(round((p["dx"] + ox) / cell))
            if r0 < 0 or c0 < 0 or r0 + mh > H or c0 + mw > W:
                continue  # defensive: shouldn't happen, skip rather than corrupt occ
            occ[r0:r0 + mh, c0:c0 + mw] &= ~m
            occ_f = np.fft.rfft2(occ.astype(np.float64), s=(H, W))
            ok = free_positions(occ_f, m, H, W)
            if ok is not None and ok.any():
                rows = np.flatnonzero(ok.any(axis=1))
                r = int(rows[0])
                c = int(np.flatnonzero(ok[r])[0])
            else:
                r, c = r0, c0  # no legal spot at all (shouldn't happen); stay put
            if (r, c) != (r0, c0):
                changed = True
            occ[r:r + mh, c:c + mw] |= m
            occ_f = np.fft.rfft2(occ.astype(np.float64), s=(H, W))
            p["dx"] = c * cell - ox
            p["dy"] = r * cell - oy
        if not changed:
            break
    return placed, occ


def trim_to_content(placed, occ, cell, aspect, margin_cells=2):
    """Crop the occupancy grid to its true ink bounding box (plus a small
    margin), then pad the shorter axis back out to exactly `aspect`
    WITHOUT touching any structure's position -- padding, never cropping
    content. Shifts every placed structure's (dx, dy) to match. Returns
    (placed, page_w, page_h)."""
    rows = np.flatnonzero(occ.any(axis=1))
    cols = np.flatnonzero(occ.any(axis=0))
    r0, r1 = int(rows[0]), int(rows[-1]) + 1
    c0, c1 = int(cols[0]), int(cols[-1]) + 1
    x0, y0 = (c0 - margin_cells) * cell, (r0 - margin_cells) * cell
    x1, y1 = (c1 + margin_cells) * cell, (r1 + margin_cells) * cell
    w, h = x1 - x0, y1 - y0
    if w / h > aspect:
        pad = (w / aspect - h) / 2
        y0 -= pad
        y1 += pad
        h = y1 - y0
    else:
        pad = (h * aspect - w) / 2
        x0 -= pad
        x1 += pad
        w = x1 - x0
    for p in placed:
        p["dx"] -= x0
        p["dy"] -= y0
    return placed, w, h


# ------------------------------------------------------------------ emitting
def _frag_element(fr, dx, dy, nid, label_size):
    fe = ET.Element("fragment", {"id": str(next(nid))})
    idmap = {}
    for n in fr["nodes"]:
        if not n.get("pos"):
            continue
        i = str(next(nid))
        idmap[n["id"]] = i
        attrs = {"id": i, "p": f"{n['pos'][0] + dx:.2f} {n['pos'][1] + dy:.2f}"}
        if n["node_type"] in NICK_TYPES:
            attrs["NodeType"] = "Unspecified"
        elif n["element"] and n["element"] != 6:
            attrs["Element"] = str(n["element"])
        if n.get("charge"):
            attrs["Charge"] = str(n["charge"])
        ne = ET.SubElement(fe, "n", attrs)
        if n["node_type"] in NICK_TYPES and n.get("label"):
            te = ET.SubElement(ne, "t", {"p": attrs["p"]})
            se = ET.SubElement(te, "s", {"font": "3", "size": f"{label_size:g}"})
            se.text = n["label"]
    for b in fr["bonds"]:
        B, E = idmap.get(b["begin"]), idmap.get(b["end"])
        if not B or not E:
            continue
        a = {"id": str(next(nid)), "B": B, "E": E}
        order = _order_int(b["order"])
        if order and order != 1:
            a["Order"] = {15: "1.5"}.get(order, str(order))
        if b.get("display"):
            a["Display"] = b["display"]
        ET.SubElement(fe, "b", a)
    return fe


def emit_page(placed, page_w, page_h, bond, id_start=100):
    """Render placed structures to a complete CDXML document string, one
    <fragment> per structure at its (dx, dy) offset. Bond length drives
    every proportional style attribute (line width, hash spacing, etc.)
    so the page stays visually consistent regardless of target_bond."""
    nid = iter(range(id_start, id_start + 8_000_000))
    label_size = bond * 0.55
    root = ET.Element("CDXML", {
        "CreationProgram": "chemdraw_connector.domain.dense_pack",
        "BondLength": f"{bond:.2f}",
        "LabelFont": "3", "CaptionFont": "3",
        "LabelSize": f"{label_size:g}", "CaptionSize": f"{label_size:g}",
        "BondSpacing": "18", "LineWidth": f"{bond * 0.045:.2f}",
        "BoldWidth": f"{bond * 0.14:.2f}", "HashSpacing": f"{bond * 0.11:.2f}",
        "MarginWidth": f"{bond * 0.07:.2f}",
        "ChainAngle": "120", "LabelJustification": "Auto",
    })
    ft = ET.SubElement(root, "fonttable")
    ET.SubElement(ft, "font", {"id": "3", "charset": "iso-8859-1", "name": "Arial"})
    page = ET.SubElement(root, "page", {
        "HeightPages": "1", "WidthPages": "1",
        "Width": f"{page_w:.2f}", "Height": f"{page_h:.2f}",
        "DrawingSpace": "Poster",
    })
    for p in placed:
        page.append(_frag_element(p["placed"], p["dx"], p["dy"], nid, label_size))
    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")


# ------------------------------------------------------------------ top-level entry points
def pack_dense_field(cdxml_text, *, page_w=1920.0, page_h=1080.0, target_bond=9.0,
                     pad_cells=0, min_atoms=10, max_atoms=55, max_defect=0.18,
                     max_aspect=4.0, max_units=4000, compact_sweeps=3, seed=7):
    """End-to-end: many structures on one input page -> a densely tiled,
    repeat-allowed output page of the given size. This is the function a
    live tool or CLI wrapper should call for "make a structure-field
    background"."""
    fragments = read_fragments(cdxml_text)
    pool_raw = dedup_pool(fragments, min_atoms, max_atoms, max_defect, max_aspect)
    cell = target_bond / 3.0
    pool = [normalise(fr, target_bond) for fr in pool_raw]
    placed, occ, cell = pack(pool, page_w, page_h, cell, pad_cells, seed, max_units)
    if compact_sweeps:
        H, W = occ.shape
        placed, occ = compact_sweep(placed, occ, cell, pad_cells, H, W, compact_sweeps, seed)
    return emit_page(placed, page_w, page_h, target_bond), {
        "unique_structures": len(pool), "placed": len(placed),
        "ink_coverage": float(occ.mean()),
    }


def pack_one_of_each(cdxml_text, *, target_bond=9.0, pad_cells=0, aspect=16 / 9,
                     min_atoms=10, max_atoms=55, max_defect=0.18, max_aspect=4.0,
                     compact_sweeps=3, seed=7):
    """End-to-end: many structures on one input page -> every unique one
    exactly once, on the smallest canvas (at the given aspect ratio) that
    fits them all."""
    fragments = read_fragments(cdxml_text)
    pool_raw = dedup_pool(fragments, min_atoms, max_atoms, max_defect, max_aspect)
    cell = target_bond / 3.0
    pool = [normalise(fr, target_bond) for fr in pool_raw]
    placed, occ, page_w, page_h, cell = compact_fit(pool, cell, pad_cells, aspect, seed)
    if compact_sweeps:
        H, W = occ.shape
        placed, occ = compact_sweep(placed, occ, cell, pad_cells, H, W, compact_sweeps, seed)
    placed, page_w, page_h = trim_to_content(placed, occ, cell, aspect)
    return emit_page(placed, page_w, page_h, target_bond), {
        "unique_structures": len(pool), "placed": len(placed),
        "page_w": page_w, "page_h": page_h,
    }
