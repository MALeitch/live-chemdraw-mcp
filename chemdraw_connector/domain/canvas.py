"""Pure canvas interpretation: classify units and compute the spatial
relationships between them — which caption labels which structure, which
panel box contains what, and what's overlapping or overflowing.

Built after a live "organize the molecules in this box" request burned ten
minutes on exactly the questions this module answers offline: half the
"structures" returned by enumeration were caption-wrapper groups with zero
atoms, box membership had to be computed by hand across several calls, and
caption ownership had to be reverse-engineered from group ids. All inputs
are the plain dicts bridge.get_layout already produces (structures with
id/atom_count/bounds, captions with text/bounds/group_id, boxes with
index/bounds) — no COM anywhere.
"""
from . import layout_math, wrapper_detection

# "Label under structure" convention: a caption within this vertical gap of
# a horizontally-overlapping structure's bottom edge is its label.
CAPTION_MAX_GAP_PT = 60.0
# Fallback for captions placed beside/inside a structure: nearest center
# within this radius. Beyond it, a caption is reported unowned rather than
# guessed at.
CAPTION_MAX_DISTANCE_PT = 120.0
# Sentinel tag_owner_id value meaning "explicitly no owner" (a reaction
# scheme's own reagents-text/"+"/arrow-fallback captions -- see
# targets.NO_CAPTION_OWNER, which this must stay in sync with; this module
# stays free of any COM/targets import by design, see the module
# docstring, so the literal is duplicated here rather than imported).
NO_CAPTION_OWNER_SENTINEL = "__no_owner__"
# How far past a box edge counts as overflowing it (drawing slop is common).
OVERFLOW_SLOP_PT = 2.0
# Bounds-containment slop for wrapper-duplicate detection.
WRAPPER_SLOP_PT = 1.5
# Largest single-edge extension a containing structure's bounds may add
# beyond a same-formula structure it contains before find_wrapper_duplicates
# still treats it as a caption band (a wrapper) rather than a second,
# larger, distinct structure. Real caption-wrapper margins observed live
# (see find_wrapper_duplicates docstring and its tests) are 15-24pt; real
# structure footprints in this codebase's examples run 80-180pt per
# dimension. 32pt gives real captions comfortable headroom (bold text,
# slightly larger font, a bit of padding) while still excluding a
# same-formula, same-size isomer whose bounds happen to fully contain
# another's by anything close to a structure-sized margin.
WRAPPER_MAX_MARGIN_PT = 32.0


def _center(b):
    return ((b["left"] + b["right"]) / 2.0, (b["top"] + b["bottom"]) / 2.0)


def _h_overlap(a, b):
    return min(a["right"], b["right"]) - max(a["left"], b["left"])


def _area(b):
    return max(0.0, b["right"] - b["left"]) * max(0.0, b["bottom"] - b["top"])


def _contains_center(box_bounds, b):
    cx, cy = _center(b)
    return (box_bounds["left"] <= cx <= box_bounds["right"]
            and box_bounds["top"] <= cy <= box_bounds["bottom"])


def _intersects(a, b):
    return (_h_overlap(a, b) > 0
            and min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]) > 0)


def union_bounds(a, b):
    return {
        "left": min(a["left"], b["left"]),
        "top": min(a["top"], b["top"]),
        "right": max(a["right"], b["right"]),
        "bottom": max(a["bottom"], b["bottom"]),
    }


def split_real_structures(units):
    """Partition a snapshot into units with atoms and units without (empty
    groups, box-frame graphics groups, standalone panel-title captions'
    groups). NOTE: having atoms is necessary but not sufficient to be a
    real structure — see find_wrapper_duplicates."""
    real, others = [], []
    for u in units:
        (real if (u.get("atom_count") or 0) > 0 else others).append(u)
    return real, others


def _contains_bounds(outer, inner, slop=WRAPPER_SLOP_PT):
    return (outer["left"] <= inner["left"] + slop
            and outer["top"] <= inner["top"] + slop
            and outer["right"] >= inner["right"] - slop
            and outer["bottom"] >= inner["bottom"] - slop)


def _containment_margin(outer, inner):
    """Largest single-edge distance `inner`'s bounds sit inside `outer`'s —
    0 for an edge that (within slop) coincides, positive for one that's
    genuinely inset. Assumes _contains_bounds(outer, inner) already holds;
    small negative slop on any one edge is floored to 0 rather than let it
    cancel out a genuinely large margin on another edge."""
    return max(0.0,
               inner["left"] - outer["left"], inner["top"] - outer["top"],
               outer["right"] - inner["right"], outer["bottom"] - inner["bottom"])


def find_wrapper_duplicates(structures):
    """{wrapper_id: wrapped_id} for phantom duplicates caused by nested
    groups.

    Probed live: a structure with a caption grouped to it enumerates as TWO
    units — the tight structure group, and an outer wrapper group holding
    structure + caption. The wrapper reports the SAME formula and atom
    count (it contains the same atoms), so atom counts can't filter it;
    what betrays it is geometry: identical formula, bounds fully containing
    the real structure's, and strictly larger (extended over the caption).
    Exact-duplicate pairs (equal bounds both ways) are left alone — those
    are real duplicates for find_duplicates to report, not wrappers.

    KNOWN BLIND SPOT: two genuinely distinct structures that happen to
    share a formula (e.g. constitutional isomers) AND happen to be laid
    out with one's bounds fully containing the other's would still be
    misclassified as a wrapper pair here — formula match plus bounds
    containment is the only signal available without a substructure
    comparison this module deliberately avoids (pure, no COM, no RDKit).
    WRAPPER_MAX_MARGIN_PT narrows the window: it requires the containing
    margin to look caption-sized (a thin band on one side) rather than
    structure-sized (see its own comment for the observed numbers this is
    based on), so two isomers would now also have to differ by less than
    that margin in every dimension to be misclassified — much narrower
    than the previous unbounded "just larger" check, though still not
    impossible for two very similarly-sized isomers. Documented, not
    eliminated; considered low-probability enough in normal figure layouts
    not to chase further without a real live-observed false positive to
    calibrate against.
    """
    out = {}
    for a in structures:
        ab = a.get("bounds")
        if not ab:
            continue
        best = None  # (inner area, id) — wrap the tightest contained unit
        for b in structures:
            if b["id"] == a["id"] or a.get("formula") != b.get("formula"):
                continue
            bb = b.get("bounds")
            if not bb or not _contains_bounds(ab, bb):
                continue
            if _area(ab) <= _area(bb) + 1.0:
                continue  # not strictly larger: exact duplicate, keep both
            if _containment_margin(ab, bb) > WRAPPER_MAX_MARGIN_PT:
                continue  # extension too large to be a caption band --
                          # likely a distinct, larger same-formula structure
            if best is None or _area(bb) < best[0]:
                best = (_area(bb), b["id"])
        if best is not None:
            out[a["id"]] = best[1]
    return out


def find_union_wrapper_duplicates(structures):
    """{wrapper_id: [wrapped_id, ...]} for phantom duplicates caused by a
    ChemDraw-created wrapper Group around MULTIPLE disconnected fragments
    -- e.g. inserting an ionic salt like "[Na+].[H-]" makes ChemDraw
    register a wrapper (formula "HNa", bounds = the union of its two
    children) alongside the two real ion fragments, which also each
    appear independently. This is a DIFFERENT wrapper shape from
    find_wrapper_duplicates' single-child caption case (a structure
    regrouped with its own caption text, same formula as the structure
    alone, extended by a thin caption-sized margin): here there are TWO OR
    MORE contained children, and the wrapper's formula is the UNION of
    its children's formulas ("HNa" contains neither "Na" nor "H" alone),
    so find_wrapper_duplicates' formula-equality precondition rejects it
    immediately and it would otherwise survive into `real` forever,
    permanently triggering false-positive overlapping_structures pairs
    against its own children on every subsequent read (confirmed live).

    Detected structurally instead of by formula: a candidate wrapper's
    bounding box must strictly contain 2+ other structures' boxes (same
    bbox-containment primitive find_wrapper_duplicates and
    _plumbing._split_wrapper_groups both use, see
    wrapper_detection.find_containing_wrappers), those contained
    structures must be pairwise DISJOINT (two real, independent fragments
    never overlap each other by construction -- if they did, this isn't
    that shape), and the wrapper's own atom_count must equal the SUM of
    its children's atom_count (replaces formula-equality, which isn't
    reliable for a union; a wrapper's atoms are exactly its children's
    atoms, nothing more).

    A single-contained-child case is intentionally left to
    find_wrapper_duplicates (not reported here) -- that's the caption
    shape, not this one."""
    with_bounds = [s for s in structures if s.get("bounds")]
    boxes = [(s["bounds"]["left"], s["bounds"]["top"],
             s["bounds"]["right"], s["bounds"]["bottom"]) for s in with_bounds]
    contain_map = wrapper_detection.find_containing_wrappers(boxes, tol=WRAPPER_SLOP_PT)
    out = {}
    for i, kid_idxs in contain_map.items():
        if len(kid_idxs) < 2:
            continue  # single-child: find_wrapper_duplicates' job, not this one's
        kids = [with_bounds[j] for j in kid_idxs]
        if any(_intersects(kids[a]["bounds"], kids[b]["bounds"])
              for a in range(len(kids)) for b in range(a + 1, len(kids))):
            continue  # children must be pairwise disjoint (real fragments never overlap)
        wrapper = with_bounds[i]
        children_atoms = sum(k.get("atom_count") or 0 for k in kids)
        if (wrapper.get("atom_count") or 0) != children_atoms:
            continue  # a real union wrapper's atoms are exactly its children's, no more/less
        out[wrapper["id"]] = [k["id"] for k in kids]
    return out


def classify_units(units):
    """The one entry point for "which of these are actual molecules":
    (real_structures, wrapper_map, union_wrapper_map, others). real
    excludes zero-atom units and BOTH wrapper shapes (single-child/caption
    duplicates via wrapper_map, multi-child/ionic-union duplicates via
    union_wrapper_map); wrapper_map maps each excluded single-child
    wrapper to the one real structure it duplicates; union_wrapper_map
    maps each excluded multi-child wrapper to the list of real structures
    it wraps; others is everything with no atoms."""
    with_atoms, others = split_real_structures(units)
    wrapper_map = find_wrapper_duplicates(with_atoms)
    union_wrapper_map = find_union_wrapper_duplicates(with_atoms)
    excluded = set(wrapper_map) | set(union_wrapper_map)
    real = [s for s in with_atoms if s["id"] not in excluded]
    return real, wrapper_map, union_wrapper_map, others


def associate_captions(structures, captions, wrapper_map=None,
                       decoration_ids=None, boxes=None):
    """For each caption, the id of the structure it labels (or None).

    Preference order:
    0a. The caption carries tag_owner_id == NO_CAPTION_OWNER_SENTINEL
        (targets.NO_CAPTION_OWNER — stamped on a reaction scheme's own
        reagents-text/"+"/arrow-fallback captions, which describe the
        WHOLE reaction rather than any one reactant/product) -> owned by
        nothing, final answer, never falls through to the spatial tiers.
        Confirmed live as a real gap before this existed: an untagged
        reagents caption sitting just above a product structure got
        matched to that product by tier 5 (nearest center) as if it were
        the product's own label.
    0b. The caption carries a `tag_owner_id` (bridge._add_caption_to_unit /
       targets.tag_caption_owner's claude_caption_owner object tag) that
       resolves to a real structure or a wrapper duplicate of one -> use it
       directly. This is the reliable tier for every caption THIS
       connector's own tools create (arrange_grid, build_scope_table,
       autonumber): `Caption.Group = unit` (tier 1 below) is a confirmed
       silent no-op on ChemDraw 26 — the assignment raises nothing, but
       reading `cap.Group` back afterward is always None — so without this
       tag, every connector-created caption would fall straight through to
       the spatial tiers (4/5), which misfire once a caption is separated
       from its structure (see fix_caption_gaps' docstring and the
       README's caption-gap incident for why that's a real, observed
       failure mode, not a hypothetical one). A tag that doesn't resolve
       to any known id (deleted structure, or a snapshot/region that
       excludes it) falls through to the tiers below rather than being
       trusted blindly.
    1. The caption's own group IS a real structure (grouped directly).
       Reliable for a caption the USER grouped with its structure by hand
       in ChemDraw's own UI — that path is not broken, only this
       connector's own `cap.Group = unit` call is (see tier 0).
    2. The caption's group is a wrapper duplicate -> the wrapped structure
       (this is how Data-inserted structures' labels are actually grouped).
    3. The caption's group is a known NON-structure unit (a standalone
       caption group) -> owned by nothing; spatial guessing must not adopt
       it.
    4. Label-below convention: the horizontally-overlapping structure whose
       bottom edge sits nearest above the caption (gap <= CAPTION_MAX_GAP_PT).
    5. Nearest structure center within CAPTION_MAX_DISTANCE_PT.

    The spatial rules (4/5) never match a structure the caption sits
    entirely ABOVE (a header like "Sillyl Identity" over a panel's first
    structure is a title, not a label — labels go below or beside), and
    when `boxes` is given, never match across panel boundaries (probed
    live: one panel's bottom structure adopted the NEXT panel's title
    sitting just below it, and a later arrange dragged that title 34 pt
    into the wrong panel).

    Returns a list parallel to `captions`.
    """
    wrapper_map = wrapper_map or {}
    decoration_ids = decoration_ids or set()
    boxes = boxes or []
    ids = {s["id"] for s in structures}
    s_box = {s["id"]: containing_box(s["bounds"], boxes)
             for s in structures if s.get("bounds")} if boxes else {}
    out = []
    for c in captions:
        tag_owner = c.get("tag_owner_id")
        if tag_owner == NO_CAPTION_OWNER_SENTINEL:
            out.append(None)
            continue
        if tag_owner is not None:
            if tag_owner in ids:
                out.append(tag_owner)
                continue
            if tag_owner in wrapper_map:
                out.append(wrapper_map[tag_owner])
                continue
        gid = c.get("group_id")
        if gid in ids:
            out.append(gid)
            continue
        if gid in wrapper_map:
            out.append(wrapper_map[gid])
            continue
        if gid in decoration_ids:
            out.append(None)
            continue
        cb = c.get("bounds")
        if not cb:
            out.append(None)
            continue
        c_box = containing_box(cb, boxes) if boxes else None
        candidates = []
        for s in structures:
            sb = s.get("bounds")
            if not sb:
                continue
            if cb["bottom"] <= sb["top"]:
                continue  # caption entirely above: a title, never a label
            if (c_box is not None and s_box.get(s["id"]) is not None
                    and c_box != s_box[s["id"]]):
                continue  # different panel box: not this structure's label
            candidates.append((s["id"], sb))
        best = None  # (gap, id)
        for sid, sb in candidates:
            if _h_overlap(sb, cb) <= 0:
                continue
            gap = cb["top"] - sb["bottom"]
            if -OVERFLOW_SLOP_PT <= gap <= CAPTION_MAX_GAP_PT:
                if best is None or gap < best[0]:
                    best = (gap, sid)
        if best is not None:
            out.append(best[1])
            continue
        cx, cy = _center(cb)
        nearest = None  # (distance, id)
        for sid, sb in candidates:
            sx, sy = _center(sb)
            d = ((sx - cx) ** 2 + (sy - cy) ** 2) ** 0.5
            if d <= CAPTION_MAX_DISTANCE_PT and (nearest is None or d < nearest[0]):
                nearest = (d, sid)
        out.append(nearest[1] if nearest else None)
    return out


def canvas_to_rows(built):
    """Flatten build_canvas()'s output into one uniform table — every
    entity on the page (structures, captions, boxes, and excluded
    wrapper/decoration units) as one row each, all sharing the same
    columns so a single CSV captures everything with one 'kind' column to
    scan down. Built for chemdraw_export_canvas_table: a full-inventory
    read is too large to return inline on a big document, so it goes to a
    file the caller pages through instead."""
    rows = []
    for s in built["structures"]:
        b = s.get("bounds") or {}
        rows.append({
            "kind": "structure", "id": s["id"],
            "label": "; ".join(s.get("captions") or []),
            "formula": s.get("formula", ""),
            "atom_count": s.get("atom_count", ""),
            "bond_count": s.get("bond_count", ""),
            "box_index": s.get("box_index", ""), "note": "",
            "left": b.get("left", ""), "top": b.get("top", ""),
            "right": b.get("right", ""), "bottom": b.get("bottom", ""),
        })
    for c in built["captions"]:
        b = c.get("bounds") or {}
        rows.append({
            "kind": "caption", "id": c.get("structure_id") or "",
            "label": c.get("text", ""), "formula": "", "atom_count": "",
            "bond_count": "", "box_index": c.get("box_index", ""), "note": "",
            "left": b.get("left", ""), "top": b.get("top", ""),
            "right": b.get("right", ""), "bottom": b.get("bottom", ""),
        })
    for bx in built["boxes"]:
        bb = bx.get("bounds") or {}
        rows.append({
            "kind": "box", "id": bx["index"],
            "label": "; ".join(bx.get("caption_texts") or []),
            "formula": "", "atom_count": "", "bond_count": "",
            "box_index": bx["index"],
            "note": f"{len(bx.get('structure_ids') or [])} structures",
            "left": bb.get("left", ""), "top": bb.get("top", ""),
            "right": bb.get("right", ""), "bottom": bb.get("bottom", ""),
        })
    for u in built["non_structure_units"]:
        rows.append({
            "kind": "excluded", "id": u["id"], "label": "", "formula": "",
            "atom_count": "", "bond_count": "", "box_index": "",
            "note": u.get("note", ""),
            "left": "", "top": "", "right": "", "bottom": "",
        })
    return rows


def containing_box(bounds, boxes):
    """Index of the smallest box whose rect contains the bounds' center —
    smallest so that nested rectangles (a page border enclosing panel boxes)
    resolve to the innermost panel. None when no box contains it."""
    best = None  # (area, index)
    for box in boxes:
        bb = box.get("bounds")
        if not bb or not _contains_center(bb, bounds):
            continue
        area = _area(bb)
        if best is None or area < best[0]:
            best = (area, box["index"])
    return best[1] if best else None


def overflow_edges(bounds, box_bounds, slop=OVERFLOW_SLOP_PT):
    """Which edges of its containing box a member's bounds cross."""
    edges = []
    if bounds["left"] < box_bounds["left"] - slop:
        edges.append("left")
    if bounds["top"] < box_bounds["top"] - slop:
        edges.append("top")
    if bounds["right"] > box_bounds["right"] + slop:
        edges.append("right")
    if bounds["bottom"] > box_bounds["bottom"] + slop:
        edges.append("bottom")
    return edges


def resolve_region(region, boxes):
    """A region spec -> a plain bounds dict. Accepts {"box_index": N}
    (a box from this document's graphics, 1-based, as reported by
    describe_canvas/get_layout) or an explicit
    {"left","top","right","bottom"}. Raises ValueError otherwise."""
    if not isinstance(region, dict):
        raise ValueError(
            f"region must be a dict ({{'box_index': N}} or an explicit rect), got {region!r}")
    if "box_index" in region:
        for box in boxes:
            if box["index"] == region["box_index"]:
                return dict(box["bounds"])
        raise ValueError(
            f"box_index {region['box_index']} not found; document has "
            f"{len(boxes)} boxes")
    missing = [k for k in ("left", "top", "right", "bottom") if k not in region]
    if missing:
        raise ValueError(f"region rect missing {missing}")
    return {k: float(region[k]) for k in ("left", "top", "right", "bottom")}


def build_canvas(units, captions, boxes, region=None,
                 page_width=None, page_height=None, doc=None):
    """Assemble the one-call semantic picture of a page.

    units/captions/boxes: the plain dicts bridge gathers (see module
    docstring). region: optional bounds dict — scopes the result to
    structures whose center falls inside it (their captions come along even
    if the caption itself pokes out), boxes that intersect it, and captions
    that are inside it or owned by a kept structure.

    page_width/page_height: the document's actual page extent (doc.Width/
    doc.Height), both required together to enable `violations.off_page` --
    structures whose bounds fall outside [0,0]..(page_width, page_height).
    Without this, nothing anywhere ever compared drawn content against the
    real page: overflowing_box only catches a structure escaping a panel
    BOX, not the page itself, and the preview image auto-crops to whatever
    was drawn so it can't show a page edge either. Omit to skip the check
    entirely (e.g. callers with no reliable page size).

    doc: optional COM document object to check for stoichiometry grid overlaps.
    """
    real, wrapper_map, union_wrapper_map, others = classify_units(units)
    # union_wrapper_map is deliberately NOT passed to associate_captions --
    # its {wrapper_id: single_structure_id} contract assumes one wrapped
    # structure per wrapper (see its own docstring, tiers 0b/2); a
    # multi-child map with list values would silently corrupt caption-
    # ownership resolution if a caption ever tagged to a union wrapper.
    # No caption should ever legitimately target one anyway (it wraps a
    # salt's ions, not a labeled structure), so it's kept fully separate.
    owner_ids = associate_captions(real, captions, wrapper_map,
                                   {u["id"] for u in others}, boxes)

    if region is not None:
        keep = {s["id"] for s in real
                if s.get("bounds") and _contains_center(region, s["bounds"])}
        real = [s for s in real if s["id"] in keep]
        kept_caps = []
        kept_owner_ids = []
        for c, oid in zip(captions, owner_ids):
            inside = c.get("bounds") and _contains_center(region, c["bounds"])
            if oid in keep or inside:
                kept_caps.append(c)
                kept_owner_ids.append(oid if oid in keep else None)
        captions, owner_ids = kept_caps, kept_owner_ids
        boxes = [b for b in boxes
                 if b.get("bounds") and _intersects(region, b["bounds"])]

    caps_by_owner = {}
    for c, oid in zip(captions, owner_ids):
        if oid is not None:
            caps_by_owner.setdefault(oid, []).append(c)

    structures_out = []
    overflowing = []
    for s in real:
        entry = dict(s)
        owned = caps_by_owner.get(s["id"], [])
        entry["captions"] = [c.get("text", "") for c in owned]
        entry["box_index"] = None
        if s.get("bounds"):
            entry["box_index"] = containing_box(s["bounds"], boxes)
            if entry["box_index"] is not None:
                bb = next(b["bounds"] for b in boxes
                          if b["index"] == entry["box_index"])
                edges = overflow_edges(s["bounds"], bb)
                if edges:
                    overflowing.append({
                        "id": s["id"], "box_index": entry["box_index"],
                        "edges": edges,
                    })
        structures_out.append(entry)

    captions_out = []
    for c, oid in zip(captions, owner_ids):
        entry = dict(c)
        entry["structure_id"] = oid
        entry["box_index"] = (containing_box(c["bounds"], boxes)
                              if c.get("bounds") else None)
        captions_out.append(entry)

    boxes_out = []
    for b in boxes:
        entry = dict(b)
        entry["structure_ids"] = [
            s["id"] for s in structures_out if s["box_index"] == b["index"]]
        entry["caption_texts"] = [
            c.get("text", "") for c in captions_out
            if c["box_index"] == b["index"]]
        boxes_out.append(entry)

    with_bounds = [s for s in structures_out if s.get("bounds")]
    overlaps = layout_math.find_overlaps(
        [layout_math.Box(**s["bounds"]) for s in with_bounds],
        ids=[s["id"] for s in with_bounds])

    # Also check overlaps with stoichiometry grids
    stoich_overlaps = []
    if doc is not None:
        for i in range(1, doc.StoichiometryGrids.Count + 1):
            try:
                grid = doc.StoichiometryGrids.Item(i)
                grid_box = layout_math.Box(grid.Left, grid.Top, grid.Right, grid.Bottom)
                grid_id = f"stoich_grid_{grid.ID}"
                for s in with_bounds:
                    s_box = layout_math.Box(**s["bounds"])
                    ox = min(s_box.right, grid_box.right) - max(s_box.left, grid_box.left)
                    oy = min(s_box.bottom, grid_box.bottom) - max(s_box.top, grid_box.top)
                    if ox > 0 and oy > 0:
                        stoich_overlaps.append((s["id"], grid_id))
                # Also check captions
                for c in captions_out:
                    if c.get("bounds"):
                        c_box = layout_math.Box(**c["bounds"])
                        ox = min(c_box.right, grid_box.right) - max(c_box.left, grid_box.left)
                        oy = min(c_box.bottom, grid_box.bottom) - max(c_box.top, grid_box.top)
                        if ox > 0 and oy > 0:
                            stoich_overlaps.append((c["id"], grid_id))
            except Exception:
                pass

    off_page = []
    if page_width is not None and page_height is not None:
        # Captions sit ~12pt below their structure's bottom edge by this
        # codebase's own convention (fix_caption_gaps/caption_anchor) --
        # a structure that's comfortably on-page can still have its
        # caption's text run past the real page edge underneath it, which
        # checking structure bounds alone would never catch even though
        # it's exactly the "content past the page edge" case this check
        # exists for.
        off_page_boxes = [layout_math.Box(**s["bounds"]) for s in with_bounds]
        off_page_ids = [s["id"] for s in with_bounds]
        for c in captions_out:
            if not c.get("bounds"):
                continue
            off_page_boxes.append(layout_math.Box(**c["bounds"]))
            owner = c.get("structure_id")
            off_page_ids.append(
                f"{owner} (caption)" if owner else f"caption {c.get('text', '')!r}")
        off_page = layout_math.page_overflow(
            off_page_boxes, off_page_ids, page_width, page_height)

    return {
        "structures": structures_out,
        "captions": captions_out,
        "boxes": boxes_out,
        "page_bounds": ({"width": page_width, "height": page_height}
                        if page_width is not None and page_height is not None
                        else None),
        "non_structure_units": [
            {"id": u["id"],
             "note": "no atoms — an empty/decoration group (box frame or "
                     "standalone caption), not a molecule; never target "
                     "this for chemistry operations"}
            for u in others
        ] + [
            {"id": wid,
             "note": f"phantom duplicate: a wrapper group around structure "
                     f"{sid} and its caption; target {sid} instead"}
            for wid, sid in wrapper_map.items()
        ] + [
            {"id": wid,
             "note": "phantom duplicate: an auto-created ionic/salt wrapper "
                     f"around structures {sids}; target those individually "
                     "instead"}
            for wid, sids in union_wrapper_map.items()
        ],
        "violations": {
            "overlapping_structures": [list(p) for p in overlaps],
            "overflowing_box": overflowing,
            "off_page": off_page,
            "stoichiometry_grid_overlaps": stoich_overlaps,
        },
    }
