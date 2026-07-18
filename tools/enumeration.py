"""Combinatorial derivative generation (RDKit; canvas only read, never written)."""
from ._common import as_json


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_enumerate_derivatives(substituents: list[str],
                                       scaffold: str = "",
                                       properties: list[str] | None = None,
                                       format: str = "smiles") -> str:
        """Generate a derivative library by substituting fragments at a marked
        attachment point, with computed properties — all via RDKit graph
        fusion + sanitization (every product is validated; failures are
        reported, never silently wrong).

        scaffold: SMILES containing one [*] attachment point; omit to read the
        currently selected ChemDraw structure (draw the R position as an
        attachment point / dummy atom). substituents: list of SMILES
        fragments — include [*] to mark the bond atom, or the first atom is
        used. properties: list from mw, exact_mass, formula, logp,
        tpsa, hbd, hba, rotatable_bonds, inchikey (default mw+formula).
        Runs without per-molecule ChemDraw round-trips, so 50+ derivatives is
        fast. Pair with chemdraw_export_data_table for a CSV."""
        return as_json(bridge.enumerate_derivatives(
            substituents, scaffold or None, format,
            properties or ["mw", "formula"]))

    @mcp.tool()
    def chemdraw_export_data_table(rows: list[dict], path: str) -> str:
        """Write tabular results to a CSV file (Excel-friendly). rows:
        list of objects, e.g. the derivatives array from
        chemdraw_enumerate_derivatives."""
        return as_json(bridge.export_data_table(rows, path))
