from chemdraw_connector.domain.layout_math import (
    Box, arrow_length_for_reagents, caption_anchor, choose_columns,
    distribute_vertical, find_overlaps, grid_positions, page_width_points,
    plan_reaction_layout, shelf_pack,
)


def boxes(n, w=50, h=40):
    return [Box(0, 0, w, h) for _ in range(n)]


def test_page_width_defaults():
    assert page_width_points("single-column") == 234.0
    assert page_width_points("double-column") == 468.0
    assert page_width_points(None, page_width_in=5.0) == 360.0


def test_choose_columns_fits_page():
    bs = boxes(10, w=100)
    cols = choose_columns(bs, page_width=468.0, h_gap=18.0)
    assert cols == 4  # 4*100 + 3*18 = 454 <= 468; 5 would be 572


def test_grid_positions_no_overlap():
    bs = boxes(6)
    positions, cols = grid_positions(bs, columns=3, page_width=468.0)
    assert cols == 3
    placed = [Box(x, y, x + b.width, y + b.height)
              for (x, y), b in zip(positions, bs)]
    assert find_overlaps(placed) == []


def test_grid_positions_rows_advance():
    bs = boxes(4)
    positions, _ = grid_positions(bs, columns=2)
    assert positions[0][1] == positions[1][1]  # same row
    assert positions[2][1] > positions[0][1]   # next row lower


def test_mixed_sizes_center_in_cells():
    bs = [Box(0, 0, 60, 40), Box(0, 0, 20, 10)]
    positions, _ = grid_positions(bs, columns=2, start_x=0, start_y=0,
                                  h_gap=0, v_gap=0, label_height=0)
    # small box centered within the 60-wide cell of column 2
    (x0, _), (x1, _) = positions
    assert x0 == 0.0
    assert x1 == 60 + (60 - 20) / 2


def test_caption_anchor_below_center():
    b = Box(0, 0, 50, 40)
    cx, cy = caption_anchor(100, 200, b)
    assert cx == 125
    assert cy > 240


def test_find_overlaps_detects_and_ignores():
    a, b, c = Box(0, 0, 50, 50), Box(40, 40, 90, 90), Box(200, 200, 250, 250)
    assert find_overlaps([a, b, c]) == [(0, 1)]
    assert find_overlaps([a, b, c], ids=["x", "y", "z"]) == [("x", "y")]


def test_empty():
    positions, cols = grid_positions([])
    assert positions == [] and cols == 1


def test_shelf_pack_empty():
    assert shelf_pack([], Box(0, 0, 100, 100)) == ([], 0.0)


def test_shelf_pack_fits_one_row():
    sizes = [(30, 20), (30, 20), (30, 20)]
    positions, overflow = shelf_pack(sizes, Box(0, 0, 100, 100), h_gap=5)
    assert overflow == 0.0
    ys = [p[1] for p in positions]
    assert ys == [0, 0, 0]  # all same row
    xs = [p[0] for p in positions]
    assert xs == [0, 35, 70]  # 30 + 5 gap each


def test_shelf_pack_wraps_to_new_row():
    # each item is 60 wide; container is 100 wide -> only 1 per row
    sizes = [(60, 20), (60, 20), (60, 20)]
    positions, overflow = shelf_pack(sizes, Box(0, 0, 100, 200), v_gap=10)
    assert overflow == 0.0
    ys = [p[1] for p in positions]
    assert ys[0] == 0
    assert ys[1] == 20 + 10  # row_h + v_gap
    assert ys[2] == 2 * (20 + 10)
    xs = [p[0] for p in positions]
    assert xs == [0, 0, 0]  # each its own row, left edge


def test_shelf_pack_reports_overflow():
    sizes = [(60, 60), (60, 60), (60, 60)]
    positions, overflow = shelf_pack(sizes, Box(0, 0, 100, 100), v_gap=10)
    # 3 rows of height 60 + 2 gaps of 10 = 200 tall, container only 100 tall
    assert overflow > 0
    assert overflow == 200 - 100


def test_shelf_pack_respects_custom_order():
    sizes = [(30, 20), (30, 20), (30, 20)]
    # place item 2 first, then 0, then 1
    positions, _ = shelf_pack(sizes, Box(0, 0, 100, 100), order=[2, 0, 1], h_gap=0)
    # positions[] stays indexed by original item index, but item 2 should
    # land in the first slot (x=0) since it was placed first in `order`
    assert positions[2][0] == 0
    assert positions[0][0] == 30
    assert positions[1][0] == 60


def test_shelf_pack_single_item_wider_than_container_still_placed():
    positions, overflow = shelf_pack([(500, 20)], Box(0, 0, 100, 100))
    assert positions[0] == (0, 0)
    assert overflow == 0.0


def test_distribute_vertical_empty():
    assert distribute_vertical([], Box(0, 0, 100, 100)) == ([], 0.0)


def test_distribute_vertical_equal_gaps_fill_height():
    # container inner height 100-2*10=80; three items of 20 -> gaps of 10
    sizes = [(40, 20), (40, 20), (40, 20)]
    positions, overflow = distribute_vertical(
        sizes, Box(0, 0, 100, 100), margin=10)
    assert overflow == 0.0
    ys = [p[1] for p in positions]
    assert ys == [10, 40, 70]
    # last item's bottom lands exactly on the inner bottom
    assert ys[-1] + 20 == 100 - 10


def test_distribute_vertical_alignments():
    sizes = [(40, 20)] * 2
    for align, expected_x in (("left", 5), ("center", 30), ("right", 55)):
        positions, _ = distribute_vertical(
            sizes, Box(0, 0, 100, 200), margin=5, align=align)
        assert positions[0][0] == expected_x, align


def test_distribute_vertical_overflow_clamps_to_min_gap():
    # three 50-tall items can't fit 100 inner height: gap clamps to min_gap
    sizes = [(40, 50)] * 3
    positions, overflow = distribute_vertical(
        sizes, Box(0, 0, 100, 100), margin=0, min_gap=4)
    ys = [p[1] for p in positions]
    assert ys == [0, 54, 108]
    assert overflow == 58.0  # bottom of last = 158 vs inner bottom 100


def test_distribute_vertical_single_item_centered():
    positions, overflow = distribute_vertical(
        [(40, 20)], Box(0, 0, 100, 100), margin=10)
    assert overflow == 0.0
    assert positions[0][1] == 40  # (80 inner - 20)/2 + 10 margin


def test_arrow_length_for_reagents_floors_at_min():
    # 5 + 20 padding = 25, well under the 70pt minimum
    assert arrow_length_for_reagents(5.0) == 70.0
    assert arrow_length_for_reagents(0.0) == 70.0


def test_arrow_length_for_reagents_boundary_at_min():
    # exactly reaches the minimum: 50 + 20 == 70
    assert arrow_length_for_reagents(50.0) == 70.0


def test_arrow_length_for_reagents_grows_with_wide_text():
    # long reagents string (e.g. "Pd(PPh3)4, K2CO3, THF/H2O, 80 C") should
    # widen the reserved arrow span past the fixed minimum
    assert arrow_length_for_reagents(200.0) == 220.0


def test_arrow_length_for_reagents_custom_min_and_padding():
    assert arrow_length_for_reagents(10.0, min_len=40.0, padding=5.0) == 40.0
    assert arrow_length_for_reagents(100.0, min_len=40.0, padding=5.0) == 105.0


def test_plan_reaction_layout_two_reactants_one_product():
    # A (w=50) + B (w=30) --arrow--> C (w=80)
    reactant_positions, product_positions, plus_positions, arrow_left_x = (
        plan_reaction_layout([[50], [30]], [[80]], arrow_len=70.0,
                             start_x=60.0, gap=24.0, plus_width=18.0))
    assert reactant_positions == [[60.0], [152.0]]
    assert plus_positions == [134.0]  # "+" between the two reactants
    assert arrow_left_x == 206.0
    assert product_positions == [[300.0]]
    # no product-side "+" since there's only one product group
    assert len(plus_positions) == 1


def test_plan_reaction_layout_multi_step_reagent_intermediate_product():
    # reagent (w=40) --arrow--> intermediate (w=60) + salt byproduct
    # (two fragments, w=20 and w=15), using non-default spacing (including
    # a distinct intra_group_gap) to exercise the parameterization.
    reactant_positions, product_positions, plus_positions, arrow_left_x = (
        plan_reaction_layout([[40]], [[60], [20, 15]], arrow_len=70.0,
                             start_x=10.0, gap=5.0, plus_width=8.0,
                             intra_group_gap=2.0))
    assert reactant_positions == [[10.0]]
    assert arrow_left_x == 55.0  # 10 + 40 + 5
    # first product group starts right after the arrow's reserved space
    assert product_positions[0] == [130.0]  # 55 + 70 + 5
    # second product group: "+" placed at 195 (130 + 60 + 5), THEN the x
    # cursor advances past plus_width before its two fragments are laid
    # out contiguously using intra_group_gap (2.0), not gap (5.0), between
    # them
    assert plus_positions == [195.0]
    assert product_positions[1] == [203.0, 225.0]  # 195 + 8 plus_width, then +20+2


def test_plan_reaction_layout_intra_group_gap_defaults_tighter_than_gap():
    # A salt byproduct's two ion fragments (w=20, w=15) should sit closer
    # together by default than two genuinely separate "+"-joined groups --
    # confirmed live that reusing the inter-group gap for this made an ion
    # pair (e.g. Na+ / OH-) read as two unrelated reactants.
    _, product_positions, _, _ = plan_reaction_layout(
        [[40]], [[20, 15]], arrow_len=70.0, start_x=10.0, gap=24.0,
        plus_width=18.0)
    salt_start, second_ion_start = product_positions[0]
    intra_gap = second_ion_start - (salt_start + 20)
    assert 0 < intra_gap < 24.0


def test_plan_reaction_layout_wide_structure_pushes_downstream_positions():
    # a very wide reactant should push the arrow and everything after it
    # further right by exactly its extra width; a very narrow one should not
    narrow_reactant, product_positions_n, _, arrow_left_narrow = (
        plan_reaction_layout([[10]], [[30]], arrow_len=70.0))
    wide_reactant, product_positions_w, _, arrow_left_wide = (
        plan_reaction_layout([[500]], [[30]], arrow_len=70.0))
    assert arrow_left_wide - arrow_left_narrow == 500.0 - 10.0
    assert (product_positions_w[0][0] - product_positions_n[0][0]
            == 500.0 - 10.0)


def test_plan_reaction_layout_no_plus_signs_for_single_groups():
    reactant_positions, product_positions, plus_positions, arrow_left_x = (
        plan_reaction_layout([[40]], [[40]], arrow_len=70.0))
    assert plus_positions == []
