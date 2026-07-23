"""Native ChemDraw Stoichiometry Grid read/write (see
chemdraw_connector/domain/stoichiometry_cdxml.py and bridge/_stoichiometry.py
for the full story on why this goes through CDXML text instead of the
live COM object model -- confirmed broken via extensive live probing).

NOTE: tool descriptions with dynamic content are passed via
@mcp.tool(description=...) -- a plain docstring is fine when there's no
shared fragment to interpolate, but an f-string is NEVER a docstring;
FastMCP would silently register the tool with no description (confirmed
live landmine, see tools/stereo.py's module docstring)."""
from ._common import as_json


def register(mcp, bridge):
    @mcp.tool(description=(
        "Build a native Stoichiometry Grid (Structure > Stoichiometry "
        "Table in ChemDraw's own UI) from EXISTING structures already on "
        "the canvas. structure_ids: a JSON list of >=2 claude_ids (from "
        "chemdraw_insert_structure or chemdraw_get_document_state) -- the "
        "reactants/products to include. CONFIRMED LIVE: this always draws "
        "one simple arrow first, below the given structures' combined "
        "bounding box and pointing right (reading-direction convention, "
        "matching chemdraw_make_reaction_scheme's own arrow), because "
        "ChemDraw's own MakeStoichiometryGrid throws a hard error when "
        "called on a selection with no arrow present -- the arrow is a "
        "real, chemically normal part of the figure (not a throwaway) "
        "and is left in place. Returns grid_index (pass to "
        "chemdraw_read_stoichiometry_table/chemdraw_edit_stoichiometry_"
        "table), the new arrow's object_id, and violations.off_page (the "
        "arrow and every given structure, checked against the document's "
        "actual page — the stoichiometry grid itself has no queryable "
        "bounds over COM, see this connector's own notes on why)."))
    def chemdraw_make_stoichiometry_table(structure_ids: list[str]) -> str:
        return as_json(bridge.make_stoichiometry_table(structure_ids))

    @mcp.tool()
    def chemdraw_read_stoichiometry_table() -> str:
        """Read every native Stoichiometry Grid on the active document
        (Structure > Stoichiometry Table in ChemDraw's own UI, or one this
        connector built by selecting reactants + an arrow before calling
        the underlying COM MakeStoichiometryGrid). Returns each grid's
        components (one per reactant/product structure, plus a read-only
        header/label component with structure_id=null) and their
        properties keyed by field name: formula, molecular_weight,
        limiting_reagent, equivalents, sample_mass, percent_weight,
        molarity, density, volume, reactant_moles, reactant_mass (plus any
        unmapped ChemDraw property under a synthetic "type_N" name -- see
        the domain module's confidence notes on the less-common fields).
        Each property reports its current value/display text, whether
        ChemDraw marks it editable, and whether it's currently rendered
        (some fields like %Weight/Molarity/Density/Volume stay hidden
        until given a non-default value). Pass a component's structure_id
        to chemdraw_edit_stoichiometry_table to change one of its editable
        fields."""
        return as_json(bridge.read_stoichiometry_tables())

    @mcp.tool(description=(
        "Edit one or more fields on ONE native Stoichiometry Grid "
        "(grid_index from chemdraw_read_stoichiometry_table) in a single "
        "batch. edits: a JSON list of {\"structure_id\": <claude_id from "
        "chemdraw_read_stoichiometry_table -- required; the header/"
        "row-label row is always read-only and can't be targeted>, "
        "\"field\": <an editable field name from "
        "chemdraw_read_stoichiometry_table, e.g. \"sample_mass\">, "
        "\"value\": <number>}. ChemDraw's own stoichiometry engine "
        "recalculates every dependent field correctly from the edited "
        "value (confirmed live: editing one reactant's sample_mass "
        "recalculated its reactant_moles correctly against the real "
        "molecular weight). "
        "IMPORTANT, confirmed live and not avoidable: this ALWAYS opens a "
        "genuinely NEW ChemDraw document window with the edit applied -- "
        "Document.Close() and reopening the same file path in place are "
        "both confirmed no-ops on this ChemDraw version, so there is no "
        "way to refresh the current window instead. Batch every value you "
        "want to change into ONE call (not one call per field) to keep "
        "this to one new window per logical edit. The response's "
        "new_active_document/new_path tell you which window is now "
        "current -- pass the PREVIOUS active document's name to "
        "chemdraw_close_document (discard_changes=true, since it's now "
        "stale/superseded) if you no longer need it, rather than leaving "
        "it open by hand."))
    def chemdraw_edit_stoichiometry_table(grid_index: int, edits: list[dict]) -> str:
        return as_json(bridge.edit_stoichiometry_table(grid_index, edits))
