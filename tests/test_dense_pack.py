import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from chemdraw_connector.domain import dense_pack as dp

# A minimal complete document: one hexagon (benzene-shaped, all-carbon) and
# one 3-carbon chain, at native bond length ~14 units, plus a nickname node
# (TES) to exercise the label-preserving path.
TWO_STRUCTURES = """<?xml version="1.0" encoding="UTF-8"?>
<CDXML>
<page>
<fragment id="100">
<n id="1" p="0 0" Element="6"/>
<n id="2" p="12.1 7" Element="6"/>
<n id="3" p="12.1 21" Element="6"/>
<n id="4" p="0 28" Element="6"/>
<n id="5" p="-12.1 21" Element="6"/>
<n id="6" p="-12.1 7" Element="6"/>
<b id="10" B="1" E="2"/>
<b id="11" B="2" E="3"/>
<b id="12" B="3" E="4"/>
<b id="13" B="4" E="5"/>
<b id="14" B="5" E="6"/>
<b id="15" B="6" E="1"/>
</fragment>
<fragment id="200">
<n id="20" p="100 0" Element="6"/>
<n id="21" p="112 7" Element="6"/>
<n id="22" p="100 14" Element="6"/>
<n id="23" p="112 21" NodeType="Nickname"><t p="112 21"><s font="3" size="10">TES</s></t></n>
<b id="30" B="20" E="21"/>
<b id="31" B="21" E="22"/>
<b id="32" B="22" E="23"/>
</fragment>
</page>
</CDXML>"""

# Same hexagon, translated + rotated -- should dedup to the SAME struct_key
# as the one in TWO_STRUCTURES despite completely different coordinates.
ROTATED_HEXAGON = """<?xml version="1.0" encoding="UTF-8"?>
<CDXML><page>
<fragment id="900">
<n id="1" p="500 500" Element="6"/>
<n id="2" p="507 512.1" Element="6"/>
<n id="3" p="521 512.1" Element="6"/>
<n id="4" p="528 500" Element="6"/>
<n id="5" p="521 487.9" Element="6"/>
<n id="6" p="507 487.9" Element="6"/>
<b id="10" B="1" E="2"/>
<b id="11" B="2" E="3"/>
<b id="12" B="3" E="4"/>
<b id="13" B="4" E="5"/>
<b id="14" B="5" E="6"/>
<b id="15" B="6" E="1"/>
</fragment>
</page></CDXML>"""

# A structure with one wildly stretched bond -- should fail the defect gate.
MANGLED = """<?xml version="1.0" encoding="UTF-8"?>
<CDXML><page>
<fragment id="1">
<n id="1" p="0 0" Element="6"/>
<n id="2" p="14 0" Element="6"/>
<n id="3" p="14 14" Element="6"/>
<n id="4" p="500 500" Element="6"/>
<b id="1" B="1" E="2"/>
<b id="2" B="2" E="3"/>
<b id="3" B="3" E="4"/>
</fragment>
</page></CDXML>"""


def _hexagon_pool_entry():
    frs = dp.read_fragments(TWO_STRUCTURES)
    return dp.normalise(dp.largest_component(frs[0]), target_bond=10.0)


# ------------------------------------------------------------------ reading
def test_read_fragments_finds_top_level_only():
    frs = dp.read_fragments(TWO_STRUCTURES)
    assert len(frs) == 2
    assert len(frs[0]["nodes"]) == 6
    assert len(frs[0]["bonds"]) == 6


def test_read_fragments_keeps_nickname_as_single_labelled_node():
    frs = dp.read_fragments(TWO_STRUCTURES)
    nick = [n for n in frs[1]["nodes"] if n["node_type"] == "Nickname"]
    assert len(nick) == 1
    assert nick[0]["label"] == "TES"


def test_largest_component_drops_disconnected_piece():
    fr = {
        "nodes": [
            {"id": "1", "element": 6, "charge": 0, "node_type": "Element", "label": "", "pos": (0, 0)},
            {"id": "2", "element": 6, "charge": 0, "node_type": "Element", "label": "", "pos": (10, 0)},
            {"id": "3", "element": 6, "charge": 0, "node_type": "Element", "label": "", "pos": (10, 10)},
            {"id": "9", "element": 11, "charge": 1, "node_type": "Element", "label": "", "pos": (500, 500)},
        ],
        "bonds": [
            {"begin": "1", "end": "2", "order": 1, "display": None},
            {"begin": "2", "end": "3", "order": 1, "display": None},
        ],
    }
    kept = dp.largest_component(fr)
    assert {n["id"] for n in kept["nodes"]} == {"1", "2", "3"}


# ------------------------------------------------------------------ quality gate
def test_usable_rejects_too_small_and_too_large():
    frs = dp.read_fragments(TWO_STRUCTURES)
    hexagon = dp.largest_component(frs[0])
    assert not dp.usable(hexagon, min_atoms=10, max_atoms=55)  # only 6 atoms
    assert dp.usable(hexagon, min_atoms=3, max_atoms=55)


def test_usable_rejects_mangled_geometry():
    frs = dp.read_fragments(MANGLED)
    fr = dp.largest_component(frs[0])
    assert not dp.usable(fr, min_atoms=3, max_atoms=55, max_defect=0.18)


def test_usable_rejects_long_thin_aspect():
    nodes = [{"id": str(i), "element": 6, "charge": 0, "node_type": "Element",
              "label": "", "pos": (i * 14.0, 0.0)} for i in range(6)]
    bonds = [{"begin": str(i), "end": str(i + 1), "order": 1, "display": None} for i in range(5)]
    fr = {"nodes": nodes, "bonds": bonds}
    assert not dp.usable(fr, min_atoms=3, max_atoms=55, max_aspect=4.0)


# ------------------------------------------------------------------ dedup
def test_struct_key_matches_translated_rotated_duplicate():
    frs_a = dp.read_fragments(TWO_STRUCTURES)
    frs_b = dp.read_fragments(ROTATED_HEXAGON)
    key_a = dp.struct_key(dp.largest_component(frs_a[0]))
    key_b = dp.struct_key(dp.largest_component(frs_b[0]))
    assert key_a == key_b  # same connectivity, coordinates irrelevant


def test_struct_key_differs_for_different_molecules():
    frs = dp.read_fragments(TWO_STRUCTURES)
    key_hexagon = dp.struct_key(dp.largest_component(frs[0]))
    key_chain = dp.struct_key(dp.largest_component(frs[1]))
    assert key_hexagon != key_chain


def test_dedup_pool_drops_duplicate_and_mangled():
    combined = dp.read_fragments(TWO_STRUCTURES) + dp.read_fragments(ROTATED_HEXAGON) + dp.read_fragments(MANGLED)
    pool = dp.dedup_pool(combined, min_atoms=3, max_atoms=55, max_defect=0.18, max_aspect=4.0)
    # hexagon (deduped against its rotated twin) + the TES chain; mangled dropped
    assert len(pool) == 2


# ------------------------------------------------------------------ geometry
def test_normalise_produces_target_bond_length():
    hexagon = _hexagon_pool_entry()
    lens = dp.bond_lengths(hexagon)
    assert lens
    assert math.isclose(statistics_median := sorted(lens)[len(lens) // 2], 10.0, rel_tol=0.05)


def test_normalise_centers_on_origin():
    hexagon = _hexagon_pool_entry()
    xs = [n["pos"][0] for n in hexagon["nodes"] if n.get("pos")]
    ys = [n["pos"][1] for n in hexagon["nodes"] if n.get("pos")]
    assert abs((min(xs) + max(xs)) / 2) < 1e-6
    assert abs((min(ys) + max(ys)) / 2) < 1e-6


def test_oriented_rotation_preserves_bond_lengths():
    hexagon = _hexagon_pool_entry()
    rotated = dp.oriented(hexagon, math.radians(37), mirror=False)
    assert sorted(round(v, 3) for v in dp.bond_lengths(hexagon)) == \
        sorted(round(v, 3) for v in dp.bond_lengths(rotated))


def test_oriented_mirror_preserves_bond_lengths():
    hexagon = _hexagon_pool_entry()
    mirrored = dp.oriented(hexagon, 0.0, mirror=True)
    assert sorted(round(v, 3) for v in dp.bond_lengths(hexagon)) == \
        sorted(round(v, 3) for v in dp.bond_lengths(mirrored))


# ------------------------------------------------------------------ collision
def test_free_positions_flags_only_non_colliding_offsets():
    occ = np.zeros((10, 10), dtype=bool)
    occ[2:5, 2:5] = True
    occ_f = np.fft.rfft2(occ.astype(np.float64), s=(10, 10))
    mask = np.ones((2, 2), dtype=bool)
    ok = dp.free_positions(occ_f, mask, 10, 10)
    # A 2x2 mask placed with its top-left at (2, 2) overlaps the occupied
    # block; placed at (0, 0) it doesn't.
    assert not ok[2, 2]
    assert ok[0, 0]


def test_free_positions_none_when_mask_bigger_than_grid():
    occ = np.zeros((5, 5), dtype=bool)
    occ_f = np.fft.rfft2(occ.astype(np.float64), s=(5, 5))
    mask = np.ones((10, 10), dtype=bool)
    assert dp.free_positions(occ_f, mask, 5, 5) is None


# ------------------------------------------------------------------ packing
def _no_overlap(placed, cell, pad_cells=0):
    H = W = 0
    stamps = []
    for p in placed:
        m, ox, oy = dp.stamp(p["placed"], cell, pad_cells)
        r0 = round((p["dy"] + oy) / cell)
        c0 = round((p["dx"] + ox) / cell)
        stamps.append((m, r0, c0))
        H = max(H, r0 + m.shape[0])
        W = max(W, c0 + m.shape[1])
    occ = np.zeros((H + 1, W + 1), dtype=bool)
    for m, r0, c0 in stamps:
        region = occ[r0:r0 + m.shape[0], c0:c0 + m.shape[1]]
        if (region & m).any():
            return False
        occ[r0:r0 + m.shape[0], c0:c0 + m.shape[1]] |= m
    return True


def test_pack_places_structures_without_overlap():
    pool = [dp.normalise(fr, 10.0) for fr in
            dp.dedup_pool(dp.read_fragments(TWO_STRUCTURES), min_atoms=3, max_atoms=55)]
    cell = 10.0 / 3.0
    placed, occ, cell = dp.pack(pool, page_w=200, page_h=200, cell=cell, pad_cells=1,
                                seed=1, max_units=50)
    assert len(placed) > 0
    assert _no_overlap(placed, cell, pad_cells=1)


def test_pack_allows_repeats():
    pool = [dp.normalise(fr, 10.0) for fr in
            dp.dedup_pool(dp.read_fragments(TWO_STRUCTURES), min_atoms=3, max_atoms=55)]
    cell = 10.0 / 3.0
    placed, occ, cell = dp.pack(pool, page_w=400, page_h=400, cell=cell, pad_cells=1,
                                seed=1, max_units=200)
    # only 2 unique structures in the pool -- filling a 400x400 page at
    # bond=10 necessarily means the same structures placed more than once
    assert len(placed) > len(pool)


def test_compact_fit_places_each_structure_exactly_once():
    pool = [dp.normalise(fr, 10.0) for fr in
            dp.dedup_pool(dp.read_fragments(TWO_STRUCTURES), min_atoms=3, max_atoms=55)]
    cell = 10.0 / 3.0
    placed, occ, w, h, cell = dp.compact_fit(pool, cell, pad_cells=0, aspect=16 / 9, seed=3)
    assert len(placed) == len(pool)
    assert _no_overlap(placed, cell, pad_cells=0)


def test_compact_sweep_preserves_piece_count_and_no_overlap():
    pool = [dp.normalise(fr, 10.0) for fr in
            dp.dedup_pool(dp.read_fragments(TWO_STRUCTURES), min_atoms=3, max_atoms=55)]
    cell = 10.0 / 3.0
    placed, occ, cell = dp.pack(pool, page_w=300, page_h=300, cell=cell, pad_cells=1,
                                seed=2, max_units=100)
    n_before = len(placed)
    H, W = occ.shape
    placed2, occ2 = dp.compact_sweep(placed, occ, cell, pad_cells=1, H=H, W=W, max_sweeps=2)
    assert len(placed2) == n_before
    assert _no_overlap(placed2, cell, pad_cells=1)


def test_compact_sweep_does_not_increase_ink_coverage_footprint():
    # Compaction should never lose or duplicate ink -- total occupied cell
    # count before and after must match exactly.
    pool = [dp.normalise(fr, 10.0) for fr in
            dp.dedup_pool(dp.read_fragments(TWO_STRUCTURES), min_atoms=3, max_atoms=55)]
    cell = 10.0 / 3.0
    placed, occ, cell = dp.pack(pool, page_w=300, page_h=300, cell=cell, pad_cells=1,
                                seed=4, max_units=100)
    before_count = int(occ.sum())
    H, W = occ.shape
    _, occ2 = dp.compact_sweep(placed, occ, cell, pad_cells=1, H=H, W=W, max_sweeps=2)
    assert int(occ2.sum()) == before_count


def test_trim_to_content_matches_target_aspect():
    pool = [dp.normalise(fr, 10.0) for fr in
            dp.dedup_pool(dp.read_fragments(TWO_STRUCTURES), min_atoms=3, max_atoms=55)]
    cell = 10.0 / 3.0
    placed, occ, w, h, cell = dp.compact_fit(pool, cell, pad_cells=0, aspect=16 / 9, seed=5)
    placed, w2, h2 = dp.trim_to_content(placed, occ, cell, aspect=16 / 9)
    assert math.isclose(w2 / h2, 16 / 9, rel_tol=1e-6)


# ------------------------------------------------------------------ emitting
def test_emit_page_round_trips_atom_and_bond_counts():
    pool = [dp.normalise(fr, 10.0) for fr in
            dp.dedup_pool(dp.read_fragments(TWO_STRUCTURES), min_atoms=3, max_atoms=55)]
    cell = 10.0 / 3.0
    placed, occ, w, h, cell = dp.compact_fit(pool, cell, pad_cells=0, aspect=16 / 9, seed=6)
    xml_text = dp.emit_page(placed, w, h, bond=10.0)
    root = ET.fromstring(xml_text)
    n_atoms = sum(1 for _ in root.iter("n"))
    n_bonds = sum(1 for _ in root.iter("b"))
    expected_atoms = sum(len(p["placed"]["nodes"]) for p in placed)
    expected_bonds = sum(len(p["placed"]["bonds"]) for p in placed)
    assert n_atoms == expected_atoms
    assert n_bonds == expected_bonds


def test_emit_page_preserves_nickname_label():
    pool = [dp.normalise(fr, 10.0) for fr in
            dp.dedup_pool(dp.read_fragments(TWO_STRUCTURES), min_atoms=3, max_atoms=55)]
    cell = 10.0 / 3.0
    placed, occ, w, h, cell = dp.compact_fit(pool, cell, pad_cells=0, aspect=16 / 9, seed=6)
    xml_text = dp.emit_page(placed, w, h, bond=10.0)
    assert "TES" in xml_text


# ------------------------------------------------------------------ top-level
def test_pack_dense_field_end_to_end():
    xml_text, stats = dp.pack_dense_field(
        TWO_STRUCTURES, page_w=300, page_h=300, target_bond=10.0,
        min_atoms=3, max_atoms=55, max_units=200, compact_sweeps=1, seed=8)
    root = ET.fromstring(xml_text)
    assert sum(1 for _ in root.iter("fragment")) == stats["placed"]
    assert stats["placed"] >= stats["unique_structures"]
    assert 0.0 < stats["ink_coverage"] < 1.0


def test_pack_one_of_each_end_to_end():
    xml_text, stats = dp.pack_one_of_each(
        TWO_STRUCTURES, target_bond=10.0, min_atoms=3, max_atoms=55,
        aspect=16 / 9, compact_sweeps=1, seed=9)
    root = ET.fromstring(xml_text)
    assert sum(1 for _ in root.iter("fragment")) == stats["unique_structures"]
    assert stats["placed"] == stats["unique_structures"]
    page = root.find("page")
    w, h = float(page.get("Width")), float(page.get("Height"))
    # rel_tol loose enough to absorb emit_page's 2-decimal-place Width/Height
    # string formatting, not a claim about packing precision
    assert math.isclose(w / h, 16 / 9, rel_tol=1e-3)
