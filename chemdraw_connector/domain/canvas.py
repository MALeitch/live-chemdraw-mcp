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
from . import layout_math

# "Label under structure" convention: a caption within this vertical gap of
# a horizontally-overlapping structure's bottom edge is its label.
CAPTION_MAX_GAP_PT = 60.0
# Fallback for captions placed beside/inside a structure: nearest center
# within this radius. Beyond it, a caption is reported unowned rather than
# guessed at.
CAPTION_MAX_DISTANCE_PT = 120.0
# How far past a box edge counts as overflowing it (drawing slop is common).
OVERFLOW_SLOP_PT = 2.0
# Bounds-containment slop for wrapper-duplicate detection.
WRAPPER_SLOP_PT = 1.5


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
            if best is None or _area(bb) < best[0]:
                best = (_area(bb), b["id"])
        if best is not None:
            out[a["id"]] = best[1]
    return out


def classify_units(units):
    """The one entry point for "which of these are actual molecules":
    (real_structures, wrapper_map, others). real excludes both zero-atom
    units and wrapper duplicates; wrapper_map maps each excluded wrapper to
    the real structure it duplicates; others is everything with no atoms."""
    with_atoms, others = split_real_structures(units)
    wrapper_map = find_wrapper_duplicates(with_atoms)
    real = [s for s in with_atoms if s["id"] not in wrapper_map]
    return real, wrapper_map, others


def associate_captions(structures, captions, wrapper_map=None,
                       decoration_ids=None, boxes=None):
    """For each caption, the id of the structure it labels (or None).

    Preference order:
    1. The caption's own group IS a real structure (grouped directly).
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


def build_canvas(units, captions, boxes, region=None):
    """Assemble the one-call semantic picture of a page.

    units/captions/boxes: the plain dicts bridge gathers (see module
    docstring). region: optional bounds dict — scopes the result to
    structures whose center falls inside it (their captions come along even
    if the caption itself pokes out), boxes that intersect it, and captions
    that are inside it or owned by a kept structure.
    """
    real, wrapper_map, others = classify_units(units)
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

    return {
        "structures": structures_out,
        "captions": captions_out,
        "boxes": boxes_out,
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
        ],
        "violations": {
            "overlapping_structures": [list(p) for p in overlaps],
            "overflowing_box": overflowing,
        },
    }
