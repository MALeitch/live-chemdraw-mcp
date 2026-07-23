"""Free-floating annotation tools: reaction/mechanism arrows and symbols.

NOTE: tool descriptions with TARGET_DOC are passed via
@mcp.tool(description=...) — an f-string is not a docstring, and FastMCP
would silently register the tool with no description.
"""
from ._common import TARGET_DOC, as_json
from .structure import _parse

_POINT_DOC = (
    "a {'x': , 'y': } point in document points (72/inch, top-left origin), "
    "or an atom_ref/1-based index/bond_ref from chemdraw_list_atoms "
    "resolved against `target` (a bond_ref resolves to its midpoint)"
)

_LIMITATION_DOC = (
    "Known limitation: this is a free-floating object anchored to a point, "
    "not bound to the atom/bond it was drawn near — moving that structure "
    "later with chemdraw_move_objects/chemdraw_transform will NOT carry it "
    "along (same class of gap as captions)."
)


def register(mcp, bridge):
    @mcp.tool(description=(
        "Create a straight reaction/mechanism arrow between two points. "
        "start/end: each " + _POINT_DOC + ". Pass target=\"\" if both "
        "start/end are raw points. head_type: solid | hollow | angle -- "
        "the FILL style of whichever end(s) have a head. start_head/"
        "tail_head: none | full | half_left | half_right -- half_left/"
        "half_right render a genuine single-barb \"fishhook\" arrowhead "
        "(confirmed live), the classic single-electron-pushing mechanism "
        "arrow shape; both \"full\" makes a double-headed arrow. Defaults "
        "match a normal single-headed reaction arrow (tail_head=none, "
        "start_head=full), same as chemdraw_make_reaction_scheme's own "
        "arrows. no_go: None | 'cross' | 'hash' marks a crossed-out \"this "
        "pathway does not occur\" indicator; dipole adds a dipole-arrow "
        "tail marker. Curved/arc arrows and true double-object ⇌ "
        "equilibrium pairs are not yet supported — both need more COM "
        "investigation before a design commitment (see "
        "docs/com_typelib/README.md). " + _LIMITATION_DOC + " " + TARGET_DOC))
    def chemdraw_make_arrow(target: str, start: dict | str, end: dict | str,
                            head_type: str = "solid", start_head: str = "full",
                            tail_head: str = "none", dashed: bool = False,
                            bold: bool = False, no_go: str = "",
                            dipole: bool = False) -> str:
        return as_json(bridge.make_arrow(
            _parse(target) if target else target, start, end, head_type,
            start_head, tail_head, dashed, bold, no_go or None, dipole))

    @mcp.tool(description=(
        "Edit an existing arrow's style (object_id from chemdraw_make_arrow "
        "or chemdraw_list_arrows — chemdraw_make_reaction_scheme also tags "
        "its own arrow, returned as arrow_object_id, so a plain reaction "
        "arrow can be restyled afterward too, e.g. into an equilibrium/"
        "retrosynthetic-style arrow). Only params explicitly passed are "
        "changed; see chemdraw_make_arrow for the vocabulary."))
    def chemdraw_set_arrow_style(object_id: str, head_type: str = "",
                                 start_head: str = "", tail_head: str = "",
                                 dashed: bool | None = None, bold: bool | None = None,
                                 no_go: str = "", dipole: bool | None = None) -> str:
        return as_json(bridge.set_arrow_style(
            object_id, head_type or None, start_head or None,
            tail_head or None, dashed, bold, no_go or None, dipole))

    @mcp.tool()
    def chemdraw_list_arrows() -> str:
        """Enumerate every arrow in the document with its object_id,
        endpoints, and head style — arrows aren't scoped to a structure,
        so this always covers the whole document."""
        return as_json(bridge.list_arrows())

    @mcp.tool(description=(
        "Place a symbol object anchored near a point. symbol_type: racemic "
        "| absolute | relative render immediately usefully as a clear "
        "boxed stereo-descriptor label (\"Rac\"/\"Abs\"/\"Rel\", confirmed "
        "live) — recommended for now. lone_pair | electron | "
        "radical_cation | radical_anion | circle_plus | circle_minus | "
        "dagger | double_dagger | plus | minus all render at a tiny "
        "(confirmed live, sub-1pt) default size with no confirmed way to "
        "resize — usable but may be barely visible in a printed figure; "
        "treat as experimental. anchor: " + _POINT_DOC + ". offset_x/"
        "offset_y: applied on top of the resolved anchor point (document "
        "points), since symbols have no automatic bond-avoidance "
        "placement. " + _LIMITATION_DOC + " " + TARGET_DOC))
    def chemdraw_make_symbol(target: str, symbol_type: str, anchor: dict | str,
                             offset_x: float = 0.0, offset_y: float = 0.0) -> str:
        return as_json(bridge.make_symbol(
            _parse(target) if target else target, symbol_type, anchor,
            offset_x, offset_y))

    @mcp.tool()
    def chemdraw_list_symbols() -> str:
        """Enumerate every symbol in the document with its object_id,
        symbol_type, and position — symbols aren't scoped to a structure,
        so this always covers the whole document."""
        return as_json(bridge.list_symbols())
