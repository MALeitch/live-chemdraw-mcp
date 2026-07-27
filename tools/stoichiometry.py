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
        "IMPORTANT, read first: the expected/theoretical mass and %yield "
        "for a product built here (both ChemDraw's own native theoretical_"
        "mass/theoretical_moles/%Yield fields, which never populate at "
        "all, and this connector's own computed_theoretical_mass/"
        "computed_percent_yield workaround) NEVER appear in the rendered "
        "ChemDraw stoichiometry table on the canvas -- they exist ONLY in "
        "chemdraw_read_stoichiometry_table's JSON response. To make the "
        "expected mass visible on the canvas itself, pass "
        "annotate_expected_mass=true to chemdraw_read_stoichiometry_table, "
        "or add your own caption. "
        "Build a native Stoichiometry Grid (Structure > Stoichiometry "
        "Table in ChemDraw's own UI) from EXISTING structures already on "
        "the canvas. reactant_ids/product_ids: JSON lists of claude_ids "
        "(from chemdraw_insert_structure or chemdraw_get_document_state), "
        "at least 1 each -- every product_ids structure must already sit "
        "to the right of every reactant_ids structure on the canvas (the "
        "layout chemdraw_make_reaction_scheme already produces satisfies "
        "this). CONFIRMED LIVE: this always draws one simple arrow "
        "HORIZONTALLY BETWEEN the reactant cluster and the product "
        "cluster -- the same left-tail/right-head geometry chemdraw_make_"
        "reaction_scheme's own arrow uses -- because ChemDraw's own "
        "MakeStoichiometryGrid throws a hard error when called on a "
        "selection with no arrow present (the arrow is a real, chemically "
        "normal part of the figure, not a throwaway, and is left in "
        "place) AND because ChemDraw infers each structure's reactant-vs-"
        "product role in the table from which side of that arrow it sits "
        "on: an arrow that doesn't sit between the two clusters (e.g. one "
        "drawn below everything's combined bounding box, this tool's own "
        "prior behavior) makes every component read back as a reactant, "
        "products included -- confirmed live and fixed 2026-07-23. A "
        "genuine product component then has NO 'Limiting?'/'Sample Mass'/"
        "'Reactant Mass' fields at all -- it exposes a different set "
        "(chemdraw_read_stoichiometry_table's actual_mass/purity/"
        "computed_percent_yield etc., see that field's own docs for which "
        "of those are cascade-confirmed vs. best-guess). "
        "IMPORTANT, confirmed to silently drop/misclassify data: every "
        "reactant_ids/product_ids structure must sit in ONE row on the "
        "canvas before calling this — ChemDraw infers each component's "
        "role from which side of a SINGLE arrow it sits on, and that "
        "arrow's position here is one average across every structure "
        "given, regardless of which row it's actually in. If your "
        "reactants/products are scattered across more than one row (e.g. "
        "a multi-step scheme), lay each row out flat first (or build one "
        "stoichiometry table per row/step) — do NOT pass ids spanning "
        "multiple rows at once. Check violations.component_mismatch "
        "(added 2026-07-24): non-null means ChemDraw silently dropped or "
        "flipped one or more components to the wrong side — it names the "
        "missing/wrong-side ids so you can fix the layout and retry "
        "rather than trusting an incomplete table. "
        "Returns grid_index "
        "(pass to chemdraw_read_stoichiometry_table/chemdraw_edit_"
        "stoichiometry_table), the new arrow's object_id, and "
        "violations.off_page (the arrow and every given structure, "
        "checked against the document's actual page — the stoichiometry "
        "grid itself has no queryable bounds over COM, see this "
        "connector's own notes on why)."))
    def chemdraw_make_stoichiometry_table(
            reactant_ids: list[str], product_ids: list[str]) -> str:
        return as_json(
            bridge.make_stoichiometry_table(reactant_ids, product_ids))

    @mcp.tool()
    def chemdraw_read_stoichiometry_table(annotate_expected_mass: bool = False) -> str:
        """IMPORTANT, read first: computed_theoretical_mass and
        computed_percent_yield below (and ChemDraw's own native theoretical_mass/
        theoretical_moles/%Yield fields, which never populate at all)
        NEVER appear in the rendered ChemDraw stoichiometry table on the
        canvas -- they exist ONLY in this call's JSON response. Pass
        annotate_expected_mass=true to also add a small "Expected: ..."
        caption under each product structure with its computed_theoretical_
        mass (skipped, no error, for any product where that value is null
        -- see the "reason" field). Calling this repeatedly with the flag
        on is safe -- it won't stack duplicate captions on the same
        structure.

        Read every native Stoichiometry Grid on the active document
        (Structure > Stoichiometry Table in ChemDraw's own UI, or one this
        connector built by selecting reactants + an arrow before calling
        the underlying COM MakeStoichiometryGrid). Returns each grid's
        components (one per reactant/product structure, plus a read-only
        header/label component with structure_id=null) and their
        properties keyed by field name. Reactant components: formula,
        molecular_weight, limiting_reagent, equivalents, sample_mass,
        percent_weight, molarity, density, volume, reactant_moles,
        reactant_mass. Product components (only reachable if the grid was
        built with a product genuinely to the right of a reactant, see
        chemdraw_make_stoichiometry_table): formula, molecular_weight,
        equivalents, actual_mass (editable -- the isolated/weighed mass),
        actual_moles and actual_mass_display (read-only, computed from
        actual_mass -- see "purity" below for the one case where it's
        NOT just actual_mass unchanged), purity (editable, HIGH
        confidence, confirmed live 2026-07-23 -- this is ChemDraw's OWN
        "Purity" field, not a yield input, despite sitting where a
        %Yield field would intuitively go: it multiplies actual_mass to
        produce actual_mass_display/"Product Mass", so setting it to
        anything other than 1 (100%) will SCALE DOWN the reported Product
        Mass away from the real isolated mass you typed into actual_mass
        (e.g. actual_mass=0.2108 with purity=0.47 reports
        actual_mass_display=0.099076, not 0.2108 -- confirmed live). Leave
        it at 1 unless you deliberately want to discount Product Mass by a
        known impurity fraction. product_percent_weight (editable,
        LOW-MEDIUM confidence, position/shape-named by analogy to the
        reactant's percent_weight -- NOT independently cascade-verified;
        see the domain module's notes), theoretical_mass/theoretical_moles
        (LOW confidence -- native ChemDraw fields that never populate via
        this connector's write path; treat as suspect). For a REAL,
        connector-verified %yield instead of trusting ChemDraw's own
        broken theoretical_mass/%Yield fields, use
        computed_theoretical_mass/computed_percent_yield -- CONNECTOR-
        COMPUTED (never a ChemDraw-native value; marked
        "connector_computed": true), derived as (limiting reagent's
        reactant_moles) x (this product's molecular_weight) for the
        theoretical mass, and (this product's actual_mass) / that
        theoretical mass for %yield. Both come back null with a "reason"
        string when the grid doesn't have exactly one reactant flagged as
        the limiting reagent, or is missing a molecular_weight/actual_mass
        needed for the math -- never a guessed number. Any unmapped
        ChemDraw property surfaces under a synthetic "type_N" name -- see
        the domain module's confidence notes for the full story on each
        field above.
        Each property reports its current value/display text, whether
        ChemDraw marks it editable, and whether it's currently rendered
        (some fields like %Weight/Molarity/Density/Volume stay hidden
        until given a non-default value). Pass a component's structure_id
        to chemdraw_edit_stoichiometry_table to change one of its editable
        fields."""
        return as_json(bridge.read_stoichiometry_tables(annotate_expected_mass))

    @mcp.tool(description=(
        "IMPORTANT, read first: the expected/theoretical mass and %yield "
        "for a product edited here NEVER appear in the rendered ChemDraw "
        "stoichiometry table on the canvas -- see chemdraw_read_"
        "stoichiometry_table's own description (annotate_expected_mass) if "
        "you want that number visible on the canvas. Also note: editing "
        "any of sample_mass/reactant_moles/density/volume for a reactant "
        "automatically recomputes and writes 'equivalents' for every "
        "reactant in the same grid in this same batch (see "
        "equivalents_recomputed/equivalents_recompute_skipped in the "
        "response) -- you don't need to also submit an explicit "
        "'equivalents' edit unless you want to override that computed "
        "value. "
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
        "current. If the PREVIOUS window was itself a throwaway from an "
        "earlier chemdraw_edit_stoichiometry_table call (not your original "
        "document), it is closed automatically -- see the response's "
        "auto_closed_previous_document. Otherwise (first edit against your "
        "real file), it's left open; pass its name to "
        "chemdraw_close_document (discard_changes=true) yourself if you "
        "no longer need it."))
    def chemdraw_edit_stoichiometry_table(grid_index: int, edits: list[dict]) -> str:
        return as_json(bridge.edit_stoichiometry_table(grid_index, edits))
