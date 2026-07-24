"""Composite full-characterization tool.

Bundles chemdraw_get_properties + chemdraw_get_iupac_name +
chemdraw_generate_characterization_text into one call, merged per
structure id, so Claude doesn't have to chain three separate tool calls
and then match their results up by id itself for an SI/manuscript
write-up.

NOTE: tool descriptions with TARGET_DOC are passed via
@mcp.tool(description=...) — an f-string is not a docstring, and FastMCP
would silently register the tool with no description. Same pattern as
tools/analysis.py.
"""
from ._common import TARGET_DOC, as_json
from .structure import _parse


def register(mcp, bridge):
    @mcp.tool(description=(
        "Full characterization writeup for the same structure(s) in ONE "
        "call: formula, molecular weight, exact (monoisotopic) mass, "
        "chemical name, and a ready-to-paste HRMS line, merged per "
        "structure by id. Replaces separately calling and then manually "
        "matching up chemdraw_get_properties + chemdraw_get_iupac_name + "
        "chemdraw_generate_characterization_text for the same target. "
        "html=true returns the name as markup with proper italics/sub/"
        "superscripts (name_html) instead of plain text (name). ion_mode: "
        "[M+H]+ | [M+Na]+ | [M+K]+ | [M+NH4]+ | [M-H]- | [M]+. "
        + TARGET_DOC))
    def chemdraw_full_characterization(
        target: str = "selection",
        html: bool = False,
        ion_mode: str = "[M+H]+",
        technique: str = "ESI",
    ) -> str:
        parsed = _parse(target)

        # get_properties is called directly (not just via
        # generate_characterization_text, which already calls it
        # internally) because generate_characterization_text's own output
        # only surfaces id/formula/text -- it drops molecular_weight and
        # exact_mass even though it computes them internally. Both raw
        # fields AND the rendered HRMS text are needed here, and neither
        # call alone provides both.
        properties = bridge.get_properties(parsed)["properties"]
        names = bridge.get_iupac_name(parsed, html)["names"]
        characterizations = bridge.generate_characterization_text(
            parsed, ion_mode, technique)["characterization"]

        names_by_id = {n["id"]: n["name"] for n in names}
        hrms_by_id = {c["id"]: c["text"] for c in characterizations}
        name_field = "name_html" if html else "name"

        records = []
        for p in properties:
            rid = p["id"]
            records.append({
                "id": rid,
                "formula": p["formula"],
                "molecular_weight": p["molecular_weight"],
                "exact_mass": p["exact_mass"],
                name_field: names_by_id.get(rid, ""),
                "hrms_text": hrms_by_id.get(rid, ""),
            })

        return as_json({"characterizations": records})
