"""Combinatorial derivative generation (RDKit; canvas only read, never written)."""
from ._common import as_json, parse_json_arg, parse_optional_json_arg


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_enumerate_derivatives(substituents_json: str,
                                       scaffold: str = "",
                                       properties_json: str = "",
                                       format: str = "smiles") -> str:
        """Generate a derivative library by substituting fragments at a marked
        attachment point, with computed properties — all via RDKit graph
        fusion + sanitization (every product is validated; failures are
        reported, never silently wrong).

        scaffold: SMILES containing one [*] attachment point; omit to read the
        currently selected ChemDraw structure (draw the R position as an
        attachment point / dummy atom). substituents_json: JSON list of SMILES
        fragments — include [*] to mark the bond atom, or the first atom is
        used. properties_json: JSON list from mw, exact_mass, formula, logp,
        tpsa, hbd, hba, rotatable_bonds, inchikey (default mw+formula).
        Runs without per-molecule ChemDraw round-trips, so 50+ derivatives is
        fast. Pair with chemdraw_export_data_table for a CSV."""
        props = parse_optional_json_arg(
            properties_json, "properties_json", list) or ["mw", "formula"]
        return as_json(bridge.enumerate_derivatives(
            parse_json_arg(substituents_json, "substituents_json", list),
            scaffold or None, format, props))

    @mcp.tool()
    def chemdraw_export_data_table(rows_json: str, path: str) -> str:
        """Write tabular results to a CSV file (Excel-friendly). rows_json:
        JSON list of objects, e.g. the derivatives array from
        chemdraw_enumerate_derivatives."""
        return as_json(bridge.export_data_table(
            parse_json_arg(rows_json, "rows_json", list), path))
