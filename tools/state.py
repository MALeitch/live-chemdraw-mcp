"""Canvas state & change awareness tools."""
from ._common import as_json


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_get_document_state() -> str:
        """Full structured snapshot of everything currently on the page —
        every structure's id, formula, counts, bounds, and position, plus any
        overlapping pairs and ChemDraw's chemical-warning count. This is the
        source of truth for canvas state; call it before manipulating anything
        you didn't just create, since the user may have edited by hand."""
        return as_json(bridge.get_document_state())

    @mcp.tool()
    def chemdraw_diff_since_last_check() -> str:
        """What changed on the canvas since you last looked: structures
        added/removed/moved/modified, computed server-side. Call this to catch
        up on the user's hand edits between your actions."""
        return as_json(bridge.diff_since_last_check())
