from chemdraw_connector.domain.layout_math import (
    Box, caption_anchor, choose_columns, distribute_vertical, find_overlaps,
    grid_positions, page_width_points, shelf_pack,
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
