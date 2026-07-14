"""Publication layout tools."""
import json

from ._common import TARGET_DOC, as_json, with_preview
from .structure import _parse


def register(mcp, bridge):
    @mcp.tool(description=(
        "START HERE for any read/interpret/organize request — ONE call "
        "returns the whole canvas classified and related: real structures "
        "(id/formula/bounds/atom counts) each with its caption texts and "
        "the panel box that contains it, captions with the structure they "
        "label, boxes with their member structure ids, and violations "
        "(overlapping structures, structures overflowing their box). "
        "Zero-atom wrapper groups are listed separately under "
        "non_structure_units so they are never mistaken for molecules. "
        "region_json optionally scopes the result to one panel: "
        "{\"box_index\": N} or {\"left\":..,\"top\":..,\"right\":..,"
        "\"bottom\":..}. Prefer this over chemdraw_get_layout plus manual "
        "geometry — do not reconstruct these relationships yourself with "
        "multiple probing calls."))
    def chemdraw_describe_canvas(region_json: str = "") -> str:
        region = json.loads(region_json) if region_json.strip() else None
        return as_json(bridge.describe_canvas(region))

    @mcp.tool(description=(
        "Organize structures INTO a region (panel box or rect) in ONE call "
        "— use this instead of computing per-object dx/dy yourself for "
        "any \"fit/organize these in the box\" request. region_json: "
        "{\"box_index\": N} (from chemdraw_describe_canvas) or an explicit "
        "rect. object_ids_json: JSON list of structure ids, placed in the "
        "given order (top-to-bottom for vertical_flow, reading order for "
        "grid). Each structure moves together with its own captions; "
        "structure sizes are NEVER changed — if the items cannot fit, the "
        "call still places them and reports it in `violations` "
        "(overflow_pt, too_wide, still_overflowing) for you to resolve "
        "(e.g. fold a branch with chemdraw_split_at_bond + "
        "chemdraw_transform, or ask the user). ALWAYS check `violations` "
        "and `unexpected_moves`, and look at the preview image. A .cdxml "
        "backup is saved first (backup_path)."))
    def chemdraw_arrange_in_region(region_json: str, object_ids_json: str,
                                   strategy: str = "vertical_flow",
                                   margin: float = 6.0, align: str = "center",
                                   h_gap: float = 8.0, v_gap: float = 8.0):
        return with_preview(bridge.arrange_in_region(
            json.loads(region_json), json.loads(object_ids_json),
            strategy, margin, align, h_gap, v_gap))

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
        "a corrective move. Each structure's own captions move with it "
        "unless move_with_captions is false. The result reports "
        "`resulting_bounds` for every moved object — use those instead of "
        "a follow-up read to verify the layout. A .cdxml backup is saved "
        "first; the result includes backup_path and a preview image."))
    def chemdraw_move_objects(moves_json: str, move_with_captions: bool = True):
        moves = json.loads(moves_json)
        return with_preview(bridge.move_objects(moves, move_with_captions))
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
