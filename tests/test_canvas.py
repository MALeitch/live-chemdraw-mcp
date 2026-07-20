"""domain/canvas.py — classification, caption ownership, box membership,
violations. Pure, no COM. Bounds mirror the live figure that motivated the
module: a tall panel box holding a vertical column of structures, each with
a "2b"-style caption beneath it, plus zero-atom caption-wrapper groups that
must never be mistaken for molecules."""
import pytest

from chemdraw_connector.domain import canvas


def _b(left, top, right, bottom):
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _structure(sid, bounds, atoms=10):
    return {"id": sid, "formula": "C6H6", "atom_count": atoms,
            "bond_count": atoms - 1, "bounds": bounds}


# ---------- split_real_structures ----------

def test_zero_atom_units_are_not_structures():
    units = [_structure("s1", _b(0, 0, 50, 50)),
             _structure("wrap1", _b(0, 0, 60, 70), atoms=0),
             {"id": "wrap2", "atom_count": None, "bond_count": None,
              "bounds": _b(5, 5, 20, 20)}]
    real, others = canvas.split_real_structures(units)
    assert [u["id"] for u in real] == ["s1"]
    assert [u["id"] for u in others] == ["wrap1", "wrap2"]


# ---------- find_wrapper_duplicates / classify_units ----------

def test_wrapper_duplicate_detected_by_containment():
    # observed live: the wrapper reports the SAME formula and atoms as the
    # structure it contains; only its bounds (extended over the caption)
    # differ
    s = _structure("s1", _b(165.7, 147.3, 344.1, 227.3), atoms=14)
    wrap = _structure("wrap1", _b(165.7, 147.3, 344.1, 251.0), atoms=14)
    assert canvas.find_wrapper_duplicates([s, wrap]) == {"wrap1": "s1"}


def test_exact_duplicates_are_not_wrappers():
    a = _structure("a", _b(0, 0, 50, 50))
    b = _structure("b", _b(0, 0, 50, 50))
    assert canvas.find_wrapper_duplicates([a, b]) == {}


def test_different_formula_containment_is_not_a_wrapper():
    small = _structure("small", _b(10, 10, 40, 40))
    big = _structure("big", _b(0, 0, 100, 100))
    big["formula"] = "C10H8"
    assert canvas.find_wrapper_duplicates([small, big]) == {}


def test_large_margin_same_formula_containment_is_not_a_wrapper():
    # A same-formula structure (e.g. a constitutional isomer) whose bounds
    # happen to fully contain a smaller one's, by a margin far bigger than
    # any real caption band observed live (15-24pt, see docstring) — this
    # looks like two distinct structures, not a caption wrapper, so
    # WRAPPER_MAX_MARGIN_PT should exclude it.
    small = _structure("small", _b(10, 10, 40, 40))
    big = _structure("big", _b(0, 0, 100, 100))  # 40pt+ margin on every edge
    assert canvas.find_wrapper_duplicates([small, big]) == {}


def test_caption_sized_margin_containment_is_still_a_wrapper():
    # Sanity check for the same threshold from the other side: a margin
    # comfortably within real caption-band territory must still register.
    s = _structure("s1", _b(0, 0, 100, 100), atoms=14)
    wrap = _structure("wrap1", _b(0, 0, 100, 120), atoms=14)  # 20pt margin
    assert canvas.find_wrapper_duplicates([s, wrap]) == {"wrap1": "s1"}


def test_classify_units_excludes_wrappers_and_empties():
    s = _structure("s1", _b(0, 0, 50, 50), atoms=14)
    wrap = _structure("wrap1", _b(0, 0, 50, 70), atoms=14)
    empty = _structure("deco1", _b(200, 200, 260, 210), atoms=0)
    real, wrapper_map, others = canvas.classify_units([s, wrap, empty])
    assert [u["id"] for u in real] == ["s1"]
    assert wrapper_map == {"wrap1": "s1"}
    assert [u["id"] for u in others] == ["deco1"]


# ---------- associate_captions ----------

def test_caption_tag_owner_wins_over_everything_else():
    # tier 0: tag_owner_id is the reliable mechanism for connector-created
    # captions (Caption.Group = unit is a confirmed no-op, so group_id is
    # always None for them) -- it must win even when a caption has drifted
    # far enough that proximity would pick a different, nearer structure.
    near = _structure("near", _b(0, 0, 50, 50))
    far = _structure("far", _b(500, 500, 550, 550))
    c = {"text": "1a, 92%", "bounds": _b(505, 555, 545, 565),
         "group_id": None, "tag_owner_id": "far"}
    assert canvas.associate_captions([near, far], [c]) == ["far"]
    # same geometry without the tag: proximity wrongly can't reach "far"
    # either (beyond CAPTION_MAX_DISTANCE_PT of "near") -- unowned, not
    # misattributed; the tag is what fixes this case, not a happy accident
    # of the fallback tiers.
    c_no_tag = dict(c, tag_owner_id=None)
    assert canvas.associate_captions([near, far], [c_no_tag]) == ["far"]


def test_caption_tag_owner_resolves_through_wrapper_map():
    s = _structure("s1", _b(0, 0, 50, 50))
    c = {"text": "2b", "bounds": _b(600, 600, 630, 610),
         "group_id": None, "tag_owner_id": "s1"}
    # tag points at s1 directly, but s1 got wrapper-mapped away (e.g. the
    # user separately hand-grouped it under a wrapper) -- still resolves.
    got = canvas.associate_captions([], [c], wrapper_map={"s1": "s1"})
    assert got == ["s1"]
    got2 = canvas.associate_captions([s], [c])
    assert got2 == ["s1"]


def test_caption_tag_owner_unresolvable_falls_through_to_group():
    # tag_owner_id points at an id not present in this call's structures or
    # wrapper_map (e.g. a deleted structure, or a region-scoped snapshot) --
    # must not be trusted blindly; falls through to the next tier instead.
    s = _structure("s1", _b(0, 0, 50, 50))
    c = {"text": "2b", "bounds": _b(300, 300, 320, 310),
         "group_id": "s1", "tag_owner_id": "claude-deleted-unit"}
    assert canvas.associate_captions([s], [c]) == ["s1"]


def test_caption_grouped_to_wrapper_labels_the_wrapped_structure():
    s = _structure("s1", _b(0, 0, 50, 50))
    c = {"text": "2b", "bounds": _b(10, 55, 40, 65), "group_id": "wrap1"}
    got = canvas.associate_captions([s], [c], wrapper_map={"wrap1": "s1"})
    assert got == ["s1"]


def test_caption_in_decoration_group_is_a_title_not_a_label():
    # panel titles like "Sillyl Identity" live in their own zero-atom
    # groups; nearby structures must not adopt them
    s = _structure("s1", _b(0, 40, 50, 90))
    c = {"text": "Sillyl Identity", "bounds": _b(5, 5, 45, 15),
         "group_id": "deco1"}
    got = canvas.associate_captions([s], [c], decoration_ids={"deco1"})
    assert got == [None]

def test_caption_grouped_directly_to_structure_wins():
    s = _structure("s1", _b(0, 0, 50, 50))
    c = {"text": "2b", "bounds": _b(300, 300, 320, 310), "group_id": "s1"}
    assert canvas.associate_captions([s], [c]) == ["s1"]


def test_caption_below_structure_is_its_label():
    s1 = _structure("s1", _b(0, 0, 50, 50))
    s2 = _structure("s2", _b(0, 100, 50, 150))
    c = {"text": "2b", "bounds": _b(10, 55, 40, 65), "group_id": "wrap1"}
    assert canvas.associate_captions([s1, s2], [c]) == ["s1"]


def test_caption_below_prefers_nearest_structure_above():
    tall = _structure("tall", _b(0, 0, 50, 90))
    short = _structure("short", _b(60, 60, 110, 90))
    # caption spans under both; both bottoms are equal, but shift tall's
    # bottom up so "short" is strictly nearer
    tall["bounds"]["bottom"] = 70.0
    c = {"text": "3a", "bounds": _b(20, 95, 90, 105), "group_id": None}
    assert canvas.associate_captions([tall, short], [c]) == ["short"]


def test_caption_beside_structure_falls_back_to_nearest_center():
    s = _structure("s1", _b(0, 0, 50, 50))
    c = {"text": "yield 92%", "bounds": _b(60, 20, 100, 30), "group_id": None}
    assert canvas.associate_captions([s], [c]) == ["s1"]


def test_title_entirely_above_structure_is_never_adopted():
    # "Sillyl Identity" sits above the panel's first structure — observed
    # live being adopted by the nearest-center fallback and then dragged
    # along by an arrange
    s = _structure("s1", _b(0, 40, 50, 90))
    c = {"text": "Sillyl Identity", "bounds": _b(5, 5, 45, 15),
         "group_id": None}
    assert canvas.associate_captions([s], [c]) == [None]


def test_caption_never_adopted_across_panel_boundary():
    # "Heterocycles", the NEXT panel's title, sits just below the previous
    # panel's bottom structure — within label-gap range, but in a
    # different box
    s = _structure("s1", _b(170, 850, 340, 934))          # in panel 2
    c = {"text": "Heterocycles", "bounds": _b(200, 980, 300, 1000),
         "group_id": None}                                # in panel 1 strip
    boxes = [{"index": 1, "bounds": _b(160, 970, 1400, 1320)},
             {"index": 2, "bounds": _b(160, 110, 360, 975)}]
    assert canvas.associate_captions([s], [c], boxes=boxes) == [None]
    # same geometry without box context: adopted by the below rule —
    # the veto is what protects it
    assert canvas.associate_captions([s], [c]) == ["s1"]


def test_faraway_caption_is_unowned_not_guessed():
    s = _structure("s1", _b(0, 0, 50, 50))
    c = {"text": "Scheme 1", "bounds": _b(500, 500, 560, 510), "group_id": None}
    assert canvas.associate_captions([s], [c]) == [None]


# ---------- containing_box / overflow_edges ----------

BOXES = [
    {"index": 1, "bounds": _b(0, 0, 600, 800)},      # page border
    {"index": 2, "bounds": _b(160, 100, 360, 700)},  # inner panel
]


def test_containing_box_resolves_to_innermost():
    assert canvas.containing_box(_b(200, 200, 300, 260), BOXES) == 2


def test_containing_box_outside_all_panels_is_page():
    assert canvas.containing_box(_b(400, 200, 500, 260), BOXES) == 1


def test_containing_box_none_when_outside_everything():
    assert canvas.containing_box(_b(900, 900, 950, 950), BOXES) is None


def test_overflow_edges_reports_crossed_edges_only():
    box = _b(160, 100, 360, 700)
    assert canvas.overflow_edges(_b(150, 200, 300, 260), box) == ["left"]
    assert canvas.overflow_edges(_b(200, 200, 300, 260), box) == []
    # within slop is not an overflow
    assert canvas.overflow_edges(_b(159, 200, 361, 260), box) == []


# ---------- resolve_region ----------

def test_resolve_region_by_box_index():
    assert canvas.resolve_region({"box_index": 2}, BOXES) == BOXES[1]["bounds"]


def test_resolve_region_explicit_rect_passthrough():
    rect = {"left": 1, "top": 2, "right": 3, "bottom": 4}
    assert canvas.resolve_region(rect, []) == \
        {"left": 1.0, "top": 2.0, "right": 3.0, "bottom": 4.0}


def test_resolve_region_rejects_unknown_box_and_partial_rect():
    with pytest.raises(ValueError):
        canvas.resolve_region({"box_index": 99}, BOXES)
    with pytest.raises(ValueError):
        canvas.resolve_region({"left": 1, "top": 2}, BOXES)
    with pytest.raises(ValueError):
        canvas.resolve_region("box 2", BOXES)


# ---------- build_canvas ----------

def _figure():
    """Panel box (index 2) holding two structures with captions beneath;
    one structure pokes out the panel's left edge; one wrapper-duplicate
    group around s2 + its caption (same formula and atoms, live-observed
    shape); one structure outside the panel overlapping nothing."""
    units = [
        _structure("s1", _b(150, 150, 340, 230)),   # overflows panel left
        _structure("s2", _b(180, 300, 340, 380)),
        _structure("s3", _b(400, 150, 500, 230)),
        _structure("wrap1", _b(180, 300, 340, 395)),  # wraps s2 + caption
    ]
    captions = [
        {"text": "2b", "bounds": _b(230, 235, 260, 245), "group_id": "wrap0"},
        {"text": "2c", "bounds": _b(230, 385, 260, 395), "group_id": "wrap1"},
        {"text": "Scheme 9", "bounds": _b(560, 750, 590, 760), "group_id": None},
    ]
    return units, captions, BOXES


def test_build_canvas_full_picture():
    units, captions, boxes = _figure()
    out = canvas.build_canvas(units, captions, boxes)

    by_id = {s["id"]: s for s in out["structures"]}
    assert set(by_id) == {"s1", "s2", "s3"}
    assert by_id["s1"]["captions"] == ["2b"]
    assert by_id["s2"]["captions"] == ["2c"]  # via wrapper_map
    assert by_id["s1"]["box_index"] == 2
    assert by_id["s3"]["box_index"] == 1

    assert [u["id"] for u in out["non_structure_units"]] == ["wrap1"]

    panel = next(b for b in out["boxes"] if b["index"] == 2)
    assert set(panel["structure_ids"]) == {"s1", "s2"}

    assert out["violations"]["overflowing_box"] == [
        {"id": "s1", "box_index": 2, "edges": ["left"]}]
    assert out["violations"]["overlapping_structures"] == []

    scheme = next(c for c in out["captions"] if c["text"] == "Scheme 9")
    assert scheme["structure_id"] is None


def test_build_canvas_reports_overlaps():
    units = [_structure("a", _b(0, 0, 100, 100)),
             _structure("b", _b(50, 50, 150, 150))]
    out = canvas.build_canvas(units, [], [])
    assert out["violations"]["overlapping_structures"] == [["a", "b"]]


def test_build_canvas_region_scopes_to_panel():
    units, captions, boxes = _figure()
    region = canvas.resolve_region({"box_index": 2}, boxes)
    out = canvas.build_canvas(units, captions, boxes, region=region)

    assert {s["id"] for s in out["structures"]} == {"s1", "s2"}
    texts = {c["text"] for c in out["captions"]}
    assert texts == {"2b", "2c"}  # owned captions kept, far caption dropped
    assert {b["index"] for b in out["boxes"]} == {1, 2}  # both intersect


# ---------- canvas_to_rows ----------

def test_canvas_to_rows_covers_every_entity_exactly_once():
    units, captions, boxes = _figure()
    out = canvas.build_canvas(units, captions, boxes)
    rows = canvas.canvas_to_rows(out)
    expected_total = (len(out["structures"]) + len(out["captions"])
                       + len(out["boxes"]) + len(out["non_structure_units"]))
    assert len(rows) == expected_total
    kinds = [r["kind"] for r in rows]
    assert kinds.count("structure") == len(out["structures"])
    assert kinds.count("caption") == len(out["captions"])
    assert kinds.count("box") == len(out["boxes"])
    assert kinds.count("excluded") == len(out["non_structure_units"])


def test_canvas_to_rows_structure_row_joins_captions_into_label():
    units, captions, boxes = _figure()
    out = canvas.build_canvas(units, captions, boxes)
    rows = canvas.canvas_to_rows(out)
    s1 = next(r for r in rows if r["kind"] == "structure" and r["id"] == "s1")
    assert s1["label"] == "2b"
    assert s1["formula"] == "C6H6"
    assert s1["box_index"] == 2


def test_canvas_to_rows_box_row_notes_member_count():
    units, captions, boxes = _figure()
    out = canvas.build_canvas(units, captions, boxes)
    rows = canvas.canvas_to_rows(out)
    panel = next(r for r in rows if r["kind"] == "box" and r["id"] == 2)
    assert panel["note"] == "2 structures"


def test_canvas_to_rows_excluded_row_carries_the_classification_note():
    units, captions, boxes = _figure()
    out = canvas.build_canvas(units, captions, boxes)
    rows = canvas.canvas_to_rows(out)
    excluded = next(r for r in rows if r["kind"] == "excluded")
    assert excluded["id"] == "wrap1"
    assert "phantom duplicate" in excluded["note"]


def test_canvas_to_rows_shares_one_column_set_across_kinds():
    units, captions, boxes = _figure()
    out = canvas.build_canvas(units, captions, boxes)
    rows = canvas.canvas_to_rows(out)
    keysets = {frozenset(r) for r in rows}
    assert len(keysets) == 1, "every row kind must share the same columns"
