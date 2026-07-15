"""CDXML -> unit bounds/atom-count/bond-count index, keyed by claude_id.

Complements domain/cdxml_graph.py (atom/bond connectivity for substructure
matching): this module reads the OTHER things state.build_snapshot needs per
unit — bounding box and visible atom/bond counts — from one CDXML export
instead of a live per-unit COM property crawl (Left/Top/Right/Bottom,
Atoms.Count, Bonds.Count — 6 COM round trips per unit, every call).

Verified live against a real 87-structure document before writing this:
claude_id object tags and BoundingBox attributes both survive CDXML export
unchanged; visible-level <n>/<b> counts (not descending into a nickname
atom's own inner <fragment>) matched COM's Atoms.Count/Bonds.Count exactly
in 6/6 spot checks, including a 1-atom nickname (NTf2 counterion) and a
0-atom decoration group. Molecular formula is NOT included here — a
nickname's true formula (e.g. "C2F6NO4S2-" for that 1-atom NTf2 node) lives
in ChemDraw's internal nickname database, not in the export; callers must
still read Formula via COM (see targets.unit_formula).

Zero COM imports; pure XML.
"""
import xml.etree.ElementTree as ET


def parse_bounds(box_str):
    """"x1 y1 x2 y2" -> {left,top,right,bottom}, normalized via min/max.

    CDX does NOT guarantee left<right/top<bottom attribute order: a
    rotated/shadowed panel-box graphic in the live document serialized as
    BoundingBox="751.16 712.10 550.36 221.70" (right,bottom,left,top) — the
    first two numbers were the LARGER x/y, not the top-left corner."""
    x1, y1, x2, y2 = (float(v) for v in box_str.split())
    return {
        "left": min(x1, x2), "right": max(x1, x2),
        "top": min(y1, y2), "bottom": max(y1, y2),
    }


def _claude_id(elem):
    for child in elem:
        if child.tag == "objecttag" and child.get("Name") == "claude_id":
            return child.get("Value")
    return None


def _count_visible(elem):
    """One count per <n>/<b>, never descending into a nickname node's own
    inner <fragment> — a contracted label (e.g. "Ph", "NTf2") is one atom
    at this level, matching what COM's Atoms.Count reports for a
    contracted structure (verified, see module docstring)."""
    n = b = 0
    for child in elem:
        if child.tag == "n":
            n += 1
            # intentionally do not descend into this node's children
        elif child.tag == "b":
            b += 1
        else:
            cn, cb = _count_visible(child)
            n += cn
            b += cb
    return n, b


def _find_tagged(elem, out):
    """Every <group>/<fragment> anywhere in the tree carrying a direct
    claude_id object tag -> out[claude_id] = element. Descends into a
    matched element's children too (not just unmatched ones) because a
    structure grouped with its own caption produces both a tagged wrapper
    group AND a tagged inner structure group, nested — both are real,
    independently-addressable units (see canvas.py's wrapper-duplicate
    handling, which relies on exactly this pair existing). Never descends
    into a <n> element's own children — that's a nickname's opaque inner
    fragment, off limits (same rule as domain/cdxml_graph.py)."""
    for child in elem:
        if child.tag == "n":
            continue
        if child.tag in ("group", "fragment"):
            cid = _claude_id(child)
            if cid:
                out[cid] = child
        _find_tagged(child, out)


def index_units(cdxml_text):
    """claude_id -> {"bounds", "atom_count", "bond_count"} for every tagged
    unit in the document. `bounds` is None if the element has no
    BoundingBox attribute (rare; callers should treat that like
    state.describe_unit's own except-fallback: position becomes
    "unknown")."""
    root = ET.fromstring(cdxml_text)
    tagged = {}
    _find_tagged(root, tagged)
    out = {}
    for cid, elem in tagged.items():
        n, b = _count_visible(elem)
        bounds_str = elem.get("BoundingBox")
        out[cid] = {
            "bounds": parse_bounds(bounds_str) if bounds_str else None,
            "atom_count": n,
            "bond_count": b,
        }
    return out
