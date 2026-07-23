"""Stereochemistry tools.

NOTE: tool descriptions with TARGET_DOC are passed via
@mcp.tool(description=...) — an f-string is not a docstring, and FastMCP
would silently register the tool with no description.
"""
from ._common import TARGET_DOC, as_json
from .structure import _parse


def register(mcp, bridge):
    @mcp.tool(description=(
        "Read stereo descriptors (R/S, E/Z) as ChemDraw derives them from "
        "the drawn wedge/hash geometry, plus each bond's display style. "
        + TARGET_DOC))
    def chemdraw_get_stereochemistry(target: str = "selection") -> str:
        return as_json(bridge.get_stereochemistry(_parse(target)))

    @mcp.tool(description=(
        "Set a bond's display: wedge | hash | wavy | bold | dash | plain. "
        "bond_index accepts either a 1-based position or a stable ref from "
        "chemdraw_list_atoms (e.g. 'b12-45'). The drawing is the source of "
        "truth for stereochemistry — R/S is derived from wedge/hash "
        "geometry, so after setting, verify the descriptor with "
        "chemdraw_get_stereochemistry and flip wedge/hash if it came out "
        "wrong. " + TARGET_DOC))
    def chemdraw_set_bond_stereo(target: str, bond_index: int | str,
                                 display: str) -> str:
        return as_json(bridge.set_bond_stereo(_parse(target), bond_index,
                                              display))

    @mcp.tool(description=(
        "Set the \"and1\"/\"or1\" relative-stereo grouping notation on one "
        "atom -- used to report a single diastereomer out of a mixture "
        "(as opposed to get/set_bond_stereo's absolute R/S wedge/hash "
        "geometry). enhanced_type: unspecified | none | absolute | or | "
        "and. group_number: which and/or group this atom belongs to (e.g. "
        "group_number=1 for \"and1\"); ignored for absolute/none. "
        "atom_index accepts either a 1-based position or a stable ref from "
        "chemdraw_list_atoms. " + TARGET_DOC))
    def chemdraw_set_enhanced_stereo(target: str, atom_index: int | str,
                                     enhanced_type: str, group_number: int = 0) -> str:
        return as_json(bridge.set_enhanced_stereo(
            _parse(target), atom_index, enhanced_type, group_number))
