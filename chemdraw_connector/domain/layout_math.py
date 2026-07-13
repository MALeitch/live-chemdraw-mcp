"""Pure grid-layout math. Coordinates are in points (72/inch), top-left origin
with y increasing downward, matching ChemDraw's document space."""
from dataclasses import dataclass

POINTS_PER_INCH = 72.0
SINGLE_COLUMN_IN = 3.25
DOUBLE_COLUMN_IN = 6.5


@dataclass
class Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    @property
    def center(self):
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)


def page_width_points(layout=None, page_width_in=None):
    if page_width_in is not None:
        return float(page_width_in) * POINTS_PER_INCH
    if layout == "single-column":
        return SINGLE_COLUMN_IN * POINTS_PER_INCH
    return DOUBLE_COLUMN_IN * POINTS_PER_INCH


def choose_columns(boxes, page_width, h_gap):
    """Most columns whose widest-cell row still fits the page width."""
    if not boxes:
        return 1
    cell_w = max(b.width for b in boxes)
    if cell_w <= 0:
        return 1
    n = int((page_width + h_gap) // (cell_w + h_gap))
    return max(1, min(n, len(boxes)))


def grid_positions(boxes, columns=None, page_width=468.0, start_x=36.0,
                   start_y=36.0, h_gap=18.0, v_gap=14.0, label_height=16.0):
    """Compute target top-left corners for each box in a uniform grid.

    Cells are uniform (max box width/height) so columns align; label_height
    reserves room under each structure for a caption. Returns a list of
    (target_left, target_top) the same length as boxes, plus the chosen
    column count.
    """
    if not boxes:
        return [], 1
    if columns is None:
        columns = choose_columns(boxes, page_width, h_gap)
    columns = max(1, int(columns))
    cell_w = max(b.width for b in boxes)
    cell_h = max(b.height for b in boxes) + label_height
    out = []
    for i, box in enumerate(boxes):
        row, col = divmod(i, columns)
        cx = start_x + col * (cell_w + h_gap) + cell_w / 2.0
        cy = start_y + row * (cell_h + v_gap) + (cell_h - label_height) / 2.0
        out.append((cx - box.width / 2.0, cy - box.height / 2.0))
    return out, columns


def caption_anchor(target_left, target_top, box, label_gap=4.0):
    """Bottom-center anchor point for a caption under a repositioned box."""
    return (target_left + box.width / 2.0, target_top + box.height + label_gap)


def find_overlaps(boxes, ids=None, tolerance=1.0):
    """Pairs of indices (or ids) whose boxes overlap by more than tolerance pt."""
    hits = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            ox = min(a.right, b.right) - max(a.left, b.left)
            oy = min(a.bottom, b.bottom) - max(a.top, b.top)
            if ox > tolerance and oy > tolerance:
                hits.append((ids[i], ids[j]) if ids else (i, j))
    return hits


def shelf_pack(item_sizes, container, order=None, h_gap=8.0, v_gap=8.0):
    """First-fit shelf packing: place item_sizes[i] = (w, h) into rows within
    `container` (a Box used as a hard boundary — e.g. an existing panel
    rectangle on a figure page), left to right, wrapping to a new row when
    the next item would cross container.right. Items are placed in `order`
    (a list of indices into item_sizes; default is 0..n-1) — this is how a
    request like "put item X right after item Y" gets expressed: reorder,
    then repack.

    Returns (positions, overflow_pt). positions[i] = (x, y) top-left corner
    for item_sizes[i] (same order as the input, regardless of `order`).
    overflow_pt is 0.0 if the packed layout fits within container.height,
    else the number of points it extends past container.bottom — the box
    boundary is a REPORTED constraint, never silently violated; the caller
    decides how to react (shrink gaps, evict an item, reject).
    """
    n = len(item_sizes)
    if n == 0:
        return [], 0.0
    if order is None:
        order = list(range(n))
    positions = [None] * n
    x, y, row_h = container.left, container.top, 0.0
    for idx in order:
        w, h = item_sizes[idx]
        if x != container.left and x + w > container.right:
            x = container.left
            y += row_h + v_gap
            row_h = 0.0
        positions[idx] = (x, y)
        x += w + h_gap
        row_h = max(row_h, h)
    overflow = max(0.0, (y + row_h) - container.bottom)
    return positions, overflow
