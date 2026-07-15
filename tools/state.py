"""Canvas state & change awareness tools."""
from ._common import as_json


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_get_document_state() -> str:
        """Full structured snapshot of every real structure (molecule)
        currently on the page — id, formula, counts, bounds, position, and
        any caption text(s) labeling it — plus overlapping pairs (among real
        structures only) and ChemDraw's chemical-warning count. Caption-
        wrapper duplicate groups and empty decoration groups (box frames,
        standalone captions) are excluded from `structures` and listed
        separately under `non_structure_units`. This is the source of truth
        for canvas state; call it before manipulating anything you didn't
        just create, since the user may have edited by hand. On a large
        page this can exceed the inline result size limit — prefer
        chemdraw_status for counts, or chemdraw_describe_canvas with
        region_json to scope to one panel, or chemdraw_export_canvas_table
        for a full-page export you page through as a file."""
        return as_json(bridge.get_document_state())

    @mcp.tool()
    def chemdraw_diff_since_last_check() -> str:
        """What changed on the canvas since you last looked: structures
        added/removed/moved/modified, computed server-side. Call this to catch
        up on the user's hand edits between your actions."""
        return as_json(bridge.diff_since_last_check())

    @mcp.tool()
    def chemdraw_export_canvas_table(path: str = "") -> str:
        """Write the FULL classified canvas inventory (every real
        structure, caption, panel box, and excluded wrapper/decoration
        unit — everything chemdraw_describe_canvas computes) to one CSV
        file instead of returning it inline. Use this for exhaustive
        review of a large page (50+ structures) where an inline
        describe_canvas/get_document_state call would exceed the
        tool-result size limit — then read the CSV with your file-reading
        tool, paging through with offset/limit as needed. Every row shares
        the same columns (kind, id, label, formula, atom_count, bond_count,
        box_index, note, bounds) with `kind` distinguishing structure /
        caption / box / excluded rows, so a single pass down the id column
        gives the whole page's inventory. For smaller asks, prefer
        chemdraw_status (counts only, including a per-box breakdown) or
        region-scoped chemdraw_describe_canvas (one panel's worth of
        detail) — both are far cheaper than a full export. path: optional;
        omit to reuse the default export location (overwritten each call,
        like the scratch document)."""
        return as_json(bridge.export_canvas_table(path or None))
