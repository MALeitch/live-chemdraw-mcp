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


def caption_anchor(target_left, target_top, box, label_gap=12.0):
    """Bottom-center anchor point for a caption under a repositioned box.

    label_gap default is 12.0, not the visually-implied 4.0: Caption.Position
    is NOT the caption's top-left corner (confirmed live — a gap of 4.0
    still left ~5pt of visible overlap with the structure above it)."""
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


def distribute_vertical(item_sizes, container, margin=6.0, min_gap=4.0,
                        align="center"):
    """One item per row, input order preserved, spread to fill the
    container's height with equal gaps — the way a column of scheme entries
    inside a panel box is typically laid out. item_sizes[i] = (w, h);
    `align` places each item horizontally: left | center | right (relative
    to the margin-inset container).

    Returns (positions, overflow_pt) like shelf_pack: positions[i] = (x, y)
    top-left corner, and overflow_pt > 0 when even at min_gap the stack
    extends past the inner bottom — reported, never silently violated. A
    single item is centered vertically instead of pinned to the top.
    """
    n = len(item_sizes)
    if n == 0:
        return [], 0.0
    inner_h = container.height - 2.0 * margin
    total_h = sum(h for _, h in item_sizes)
    if n == 1:
        gap = 0.0
        y = container.top + margin + max(0.0, (inner_h - total_h) / 2.0)
    else:
        gap = max((inner_h - total_h) / (n - 1), min_gap)
        y = container.top + margin
    positions = []
    for w, h in item_sizes:
        if align == "left":
            x = container.left + margin
        elif align == "right":
            x = container.right - margin - w
        else:
            x = container.left + (container.width - w) / 2.0
        positions.append((x, y))
        y += h + gap
    bottom_of_last = y - gap
    overflow = max(0.0, bottom_of_last - (container.bottom - margin))
    return positions, round(overflow, 1)


def stoichiometry_arrow_span(reactant_right, product_left, gap=24.0):
    """x-span for chemdraw_make_stoichiometry_table's arrow: horizontally
    BETWEEN the reactant cluster's rightmost edge and the product
    cluster's leftmost edge -- not below everything's combined bounding
    box (that placement is confirmed live to make ChemDraw's own
    MakeStoichiometryGrid classify every component as a reactant,
    products included, since none of them then sits on either "side" of
    the arrow -- see bridge/_stoichiometry.py's module docstring for the
    full investigation).

    Returns (arrow_left_x, arrow_right_x). Raises ValueError if
    product_left <= reactant_right (products must already sit to the
    right of reactants on the canvas -- this function can't fix that,
    only detect it). The requested gap shrinks gracefully (down to
    available/3 on each side) rather than raising when the two clusters
    sit close together, but a positive available span always yields
    arrow_right_x > arrow_left_x."""
    available = product_left - reactant_right
    if available <= 0:
        raise ValueError(
            f"product_left ({product_left}) must be greater than "
            f"reactant_right ({reactant_right}) -- products must sit to "
            "the right of reactants for ChemDraw to classify them "
            "correctly."
        )
    gap = min(gap, available / 3.0)
    return reactant_right + gap, product_left - gap


def arrow_length_for_reagents(reagents_width, min_len=70.0, padding=20.0):
    """Reserved arrow span for a reaction scheme's reagents/conditions text.

    Grows with the reagents-text width instead of staying a fixed length
    regardless of how long the conditions string is -- CleanRXN+'s (a
    separate, working ChemDraw add-in for reaction-scheme layout)
    auto-width rule: arrow space = max(default, reagent text width +
    padding)."""
    return max(min_len, reagents_width + padding)


def plan_reaction_layout(reactant_groups, product_groups, arrow_len,
                         start_x=60.0, gap=24.0, plus_width=18.0,
                         intra_group_gap=8.0):
    """Plan x-positions for a one-row reactants -> arrow -> products scheme.

    reactant_groups/product_groups: each a list of "groups", where a group
    is the list of unit widths belonging to one logical structure (usually
    one width, but a structure that ChemDraw splits into several placed
    objects -- e.g. a salt's ion pair -- is a group of more than one width).
    A "+" caption is placed BETWEEN groups, never within one; units WITHIN a
    group are laid out contiguously using `intra_group_gap`, not `gap` --
    confirmed live that reusing the same 24pt inter-group gap made a salt's
    ion pair (e.g. Na+ / OH-) read as two unrelated reactants rather than
    one structure, since it's the same spacing used across an actual "+".
    `intra_group_gap` defaults much tighter so a multi-fragment structure
    still visually reads as one unit. The trailing gap after a group's LAST
    fragment (leading into the next "+"/group or the arrow) is always the
    full `gap`, unaffected by `intra_group_gap`. arrow_len is the
    already-sized arrow span (see arrow_length_for_reagents) reserved
    between the last reactant group and the first product group, with `gap`
    on both sides of it.

    Returns (reactant_positions, product_positions, plus_positions,
    arrow_left_x):
    - reactant_positions/product_positions: lists parallel to the input
      groups, each itself a list of target_left floats parallel to that
      group's unit widths.
    - plus_positions: the LEFT EDGE of each reserved plus_width-wide "+"
      slot (not a center — the slot's own center is
      plus_position + plus_width / 2), reactants' internal ones (if any)
      followed by products' (if any).
    - arrow_left_x: the arrow's left edge.
    """
    x = start_x
    plus_positions = []

    def lay_out(groups):
        nonlocal x
        positions = []
        for i, widths in enumerate(groups):
            if i:
                plus_positions.append(x)
                x += plus_width
            group_positions = []
            last = len(widths) - 1
            for j, w in enumerate(widths):
                group_positions.append(x)
                x += w + (gap if j == last else intra_group_gap)
            positions.append(group_positions)
        return positions

    reactant_positions = lay_out(reactant_groups)
    arrow_left_x = x
    x += arrow_len + gap
    product_positions = lay_out(product_groups)
    return reactant_positions, product_positions, plus_positions, arrow_left_x


def page_overflow(boxes, ids, page_width, page_height, slop=1.0):
    """Which of `boxes` (parallel to `ids`) fall outside the actual page --
    [0, 0] to (page_width, page_height), the document's real print/export
    extent (doc.Width/doc.Height) -- and on which edge(s).

    Every layout tool already verifies overlaps and box-region fit, but
    nothing previously checked against the PAGE itself: preview_png_base64
    is generated by exporting doc.Objects, which auto-crops to whatever was
    drawn -- content sitting past the page edge renders exactly as cleanly
    as content that doesn't, so the preview image can never reveal this on
    its own. Same "reported, never silently violated" contract as
    find_overlaps/shelf_pack's overflow -- the caller decides how to react
    (shrink, reflow, ask the user)."""
    hits = []
    for box, oid in zip(boxes, ids):
        edges = []
        if box.left < -slop:
            edges.append("left")
        if box.top < -slop:
            edges.append("top")
        if box.right > page_width + slop:
            edges.append("right")
        if box.bottom > page_height + slop:
            edges.append("bottom")
        if edges:
            hits.append({"id": oid, "edges": edges})
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
