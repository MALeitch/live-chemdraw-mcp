"""Properties, naming, characterization, QC tools.

NOTE: tool descriptions with TARGET_DOC are passed via
@mcp.tool(description=...) — an f-string is not a docstring, and FastMCP
would silently register the tool with no description.
"""
from ._common import TARGET_DOC, as_json
from .structure import _parse


def register(mcp, bridge):
    @mcp.tool(description=(
        "Formula, molecular weight, and exact (monoisotopic) mass per "
        "structure. " + TARGET_DOC))
    def chemdraw_get_properties(target: str = "selection") -> str:
        return as_json(bridge.get_properties(_parse(target)))

    @mcp.tool(description=(
        "Generate the chemical name of drawn structures using ChemDraw's "
        "structure-to-name engine. html=true returns markup with proper "
        "italics/sub/superscripts. " + TARGET_DOC))
    def chemdraw_get_iupac_name(target: str = "selection",
                                html: bool = False) -> str:
        return as_json(bridge.get_iupac_name(_parse(target), html))

    @mcp.tool(description=(
        "Ready-to-paste HRMS lines for SI/manuscripts, e.g. \"HRMS (ESI) m/z "
        "calc'd for C9H8O4 [M+H]+: 181.0495, found: ____.\" ion_mode: [M+H]+ "
        "| [M+Na]+ | [M+K]+ | [M+NH4]+ | [M-H]- | [M]+. " + TARGET_DOC))
    def chemdraw_generate_characterization_text(
        target: str = "selection",
        ion_mode: str = "[M+H]+",
        technique: str = "ESI",
    ) -> str:
        return as_json(bridge.generate_characterization_text(
            _parse(target), ion_mode, technique))

    @mcp.tool(description=(
        "Find duplicate structures by InChIKey — 'exact' groups are true "
        "duplicates; 'skeleton' groups share connectivity but differ in "
        "stereo/tautomer form. Useful before submitting a scope figure. "
        + TARGET_DOC))
    def chemdraw_find_duplicates(target: str = "document") -> str:
        return as_json(bridge.find_duplicates(_parse(target)))

    @mcp.tool(description=(
        "Run ChemDraw's own chemical sanity check: lists every atom/bond it "
        "flags (valence errors etc.) with the warning text. " + TARGET_DOC))
    def chemdraw_check_warnings(target: str = "document") -> str:
        return as_json(bridge.check_warnings(_parse(target)))
