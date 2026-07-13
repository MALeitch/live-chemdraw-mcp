"""Publication layout tools."""
import json

from ._common import TARGET_DOC, as_json, with_preview
from .structure import _parse


def register(mcp, bridge):
    @mcp.tool(description=(
        "Full layout snapshot for PLANNING a reorganization: every "
        "structure's id/formula/bounds, every caption's text and which "
        "structure it belongs to, and every panel rectangle on the page — "
        "all in one call. Use this FIRST for any \"move/rearrange these "
        "structures\" request, then compute the full target layout "
        "yourself (positions, which existing panel each item should land "
        "in, whether it fits within panel bounds) before calling "
        "chemdraw_move_objects — do not probe bounds one object at a time "
        "and move things incrementally; that is much slower and more "
        "error-prone than planning first, moving once. " + TARGET_DOC +
        " Captions and panel rectangles are always returned for the whole "
        "page regardless of target, since planning needs that context."))
    def chemdraw_get_layout(target: str = "document") -> str:
        return as_json(bridge.get_layout(_parse(target)))

    @mcp.tool(description=(
        "Apply many independent moves in ONE call instead of one call per "
        "object — the execution half of a plan built from "
        "chemdraw_get_layout's output. moves_json: JSON list like "
        "[{\"object_id\": \"claude-...\", \"dx\": 12.0, \"dy\": -8.0}, ...] "
        "(points, 72/inch). Every call snapshots the document before and "
        "after and reports `unexpected_moves` — any object that moved "
        "WITHOUT being in your list (e.g. a counterion or satellite "
        "fragment that turned out to be carried along by a different "
        "object's move). ALWAYS check `unexpected_moves` in the result; if "
        "it's non-empty, something you didn't plan for shifted and needs "
        "a corrective move. A .cdxml backup is saved first; the result "
        "includes backup_path and a preview image."))
    def chemdraw_move_objects(moves_json: str):
        moves = json.loads(moves_json)
        return with_preview(bridge.move_objects(moves))
    @mcp.tool()
    def chemdraw_arrange_grid(items_json: str, columns: int = 0,
                              layout: str = "double-column",
                              page_width_in: float = 0):
        """Arrange existing structures into a publication grid with optional
        labels beneath each. items_json: JSON list like
        [{"object_id": "claude-...", "label": "3a, 85%"}, ...].
        layout: single-column (3.25 in) | double-column (6.5 in), or set
        page_width_in explicitly; columns=0 auto-fits.

        The response includes a rendered preview image — LOOK AT IT and
        re-invoke with different columns/ordering if anything overlaps, labels
        wrap awkwardly, or the grid is unbalanced. A document backup is saved
        first (backup_path) for rollback."""
        items = json.loads(items_json)
        return with_preview(bridge.arrange_grid(
            items, columns or None, layout, page_width_in or None))

    @mcp.tool()
    def chemdraw_build_scope_table(entries_json: str, columns: int = 0,
                                   layout: str = "double-column",
                                   page_width_in: float = 0):
        """Build an entire substrate-scope table in one call: insert every
        structure, place its label (name/yield) beneath it, and arrange the
        grid. entries_json: JSON list like
        [{"representation": "c1ccccc1", "format": "smiles", "label": "1a, 92%"}, ...]
        (format defaults to smiles; also accepts name/molfile/inchi).

        The response includes a rendered preview image — LOOK AT IT and
        re-invoke or rearrange if anything is off. A document backup is saved
        first (backup_path)."""
        entries = json.loads(entries_json)
        return with_preview(bridge.build_scope_table(
            entries, columns or None, layout, page_width_in or None))

    @mcp.tool()
    def chemdraw_autonumber(target: str = "document", start: int = 1,
                            scheme: str = "numeric", bold: bool = True) -> str:
        """Stamp compound numbers (1, 2, 3...) beneath structures in reading
        order (top-to-bottom, left-to-right). scheme: numeric | numeric-letter."""
        return as_json(bridge.autonumber(_parse(target), start, scheme, bold))
