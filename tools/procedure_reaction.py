"""Composite reaction-scheme tools: single-step verification, and
multi-step routes.

Bundles chemdraw_make_reaction_scheme with the QC calls a careful user
would always make right afterward, so Claude doesn't have to decide (and
remember) to run them as separate follow-up tool calls. Also provides
chemdraw_make_reaction_route, a thin loop over the single-step builder for
drawing a whole multi-step synthesis at once."""
from ._common import with_preview


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_make_reaction_scheme_verified(
        reactants: list[str], products: list[str],
        reagents_text: str = "",
        format: str = "smiles",
        conditions_text: str = "",
        anchor_y: float | None = None,
        yields: list | None = None):
        """Draw a reaction scheme AND auto-verify it in one call: this is
        chemdraw_make_reaction_scheme immediately followed by
        chemdraw_check_warnings (scoped to just the structures this call
        drew) and chemdraw_find_duplicates (scoped to the whole document)
        — you do NOT need to make either of those calls yourself
        afterward, they are already included in this result.

        Auto-appends below existing content by default: if the canvas
        already has structures/captions/arrows on it, this scheme is drawn
        BELOW the lowest existing one rather than always at the same fixed
        row — calling this tool twice in a row (e.g. to draw a second
        reaction step) no longer silently draws on top of the first scheme
        with no warning (confirmed live bug, fixed 2026-07-24). Pass
        anchor_y explicitly to override auto-append and force a specific
        row instead.

        Standard single-step-scheme convention (ACS style and similar
        journals) — use BOTH text params, not just reagents_text, for any
        real reaction write-up:
        - reagents_text: reagents/catalysts, placed ABOVE the arrow (e.g.
          "Pd(OAc)2, PPh3").
        - conditions_text: solvent, temperature, time, and other physical
          conditions — including things like irradiation wavelength or
          "microwave" — placed BELOW the arrow (e.g. "K2CO3, DMF, 120 °C,
          12 h"). K2CO3-style formula digits are rendered as real
          ChemDraw subscripts automatically in EITHER field, not just
          reagents_text.
        Passing only reagents_text still works (single line above the
        arrow, the original behavior) for a quick/informal scheme, but a
        publication-style scheme should split reagents vs. conditions
        across both params rather than cramming everything into
        reagents_text.

        reactants/products: lists of structures in the given
        format, e.g. ["CC(=O)Cl", "c1ccccc1O"]. Includes a preview image —
        look at it and adjust if the layout needs work.

        Result fields, beyond the scheme itself:
        - object_ids, arrow_native, arrow_object_id, backup_path: same as
          chemdraw_make_reaction_scheme.
        - violations: ALWAYS check `violations.overlapping` (every
          structure, the arrow, and every caption, checked pairwise after
          layout), `violations.mislaid_captions` (a caption/arrow label
          that didn't land where intended even after one retry — should
          be empty in normal use), and `violations.off_page` (a wide
          scheme can extend past the document's actual page even though
          it renders cleanly in the preview image, which auto-crops to
          whatever was drawn rather than the page boundary).
        - warnings: ChemDraw's own chemical sanity check
          (chemdraw_check_warnings), run automatically against only the
          reactant/product structures just drawn (not the whole
          document). Check `warnings.flagged` — any entry means ChemDraw
          flagged a valence error or similar on one of the structures you
          just inserted; look at the preview image and fix the offending
          structure (or the source SMILES/name) before treating the
          scheme as final.
        - duplicate_groups: chemdraw_find_duplicates run automatically
          over the WHOLE document (not just this scheme), since the
          point is to catch this scheme accidentally re-drawing a
          structure that already exists elsewhere on the page. Any
          "exact" group means true duplicate structures somewhere in the
          document; a "skeleton" group shares connectivity but differs in
          stereo/tautomer form — review both before finalizing, e.g. by
          removing the redundant copy or confirming the stereo/tautomer
          difference is intentional.

        If warnings, duplicate_groups, or violations come back non-empty,
        do not just report the scheme as done — resolve each one (edit
        the flagged atom/bond, deduplicate the repeated structure, fix
        the overlap/off-page placement) or explicitly flag it to the user
        before moving on.

        yields: optional list parallel to products (one entry per
        product). A number auto-formats as a percent ("92" -> "92%"); a
        string is used as-is ("quant.", "85% (2 steps)"); None/omitted
        means no caption for that product."""
        result = bridge.make_reaction_scheme(
            reactants, products, reagents_text or None, format,
            conditions_text or None, anchor_y, yields)

        object_ids = result.get("object_ids") or []
        result["warnings"] = bridge.check_warnings(object_ids)
        result["duplicate_groups"] = bridge.find_duplicates("document")

        return with_preview(result)

    @mcp.tool()
    def chemdraw_make_reaction_route(steps: list[dict]):
        """Draw a multi-step synthesis route: one reaction scheme per step,
        auto-stacked top to bottom on the page in order (no need to manage
        vertical position yourself). Each step dict:
        - reactants, products (REQUIRED): lists of structures, e.g.
          ["CC(=O)Cl", "c1ccccc1O"].
        - format (optional, default "smiles"): applies to that step's
          reactants/products only -- different steps may use different
          formats.
        - reagents_text, conditions_text (optional): same meaning as
          chemdraw_make_reaction_scheme_verified -- reagents/catalysts
          above the arrow, solvent/temperature/time/special conditions
          below it.
        - yields (optional): list parallel to that step's products, one
          entry per product. A number auto-formats as a percent ("92" ->
          "92%"); a string is used as-is ("quant.", "85% (2 steps)").

        A compound that's both one step's product and the next step's
        reactant (a common intermediate) should just have its SMILES
        repeated in both steps -- this matches how published multi-step
        routes are actually drawn (each step is visually self-contained),
        not a workaround. Because of that, `duplicate_groups` (checked
        once over the WHOLE route at the end, not per step) will
        correctly flag that repeated intermediate -- that is EXPECTED for
        any real chained route, not a bug to fix.

        Each step is independent: if one step fails to DRAW (bad SMILES, a
        missing reactants/products key, etc.) it's reported in `failed`
        by its step_index and error, but every OTHER step still gets
        drawn -- the whole route never aborts because of one bad step.

        Result fields:
        - steps: one entry per step that DREW successfully, each with the
          same object_ids/arrow_object_id/violations/warnings fields as
          chemdraw_make_reaction_scheme_verified, plus its own step_index.
          ALWAYS check each step's violations.overlapping/
          mislaid_captions/off_page and warnings.flagged the same way you
          would for a single scheme. A step that drew fine but whose
          follow-up chemical-warning check itself failed still appears
          here (its structures/arrow/captions are really on the canvas --
          it does NOT get demoted to `failed`) with `warnings: null` and
          a `warnings_error` message instead; treat that as "warnings
          unavailable for this step", not as a drawing failure.
        - failed: steps that never drew at all, with step_index and error.
        - duplicate_groups: chemdraw_find_duplicates over the whole
          document, checked once after every step is drawn -- see the
          repeated-intermediate note above before treating any entry here
          as a problem.

        Includes one preview image at the end (the whole document after
        the last successfully-drawn step, not per-step) -- look at it to
        confirm the whole route reads correctly top to bottom."""
        result = bridge.make_reaction_route(steps)
        preview = None
        for step in result["steps"]:
            png = step.pop("preview_png_base64", None)
            if png:
                preview = png  # each step's own preview is already a
                                # whole-document render; keep the last one
        result["preview_png_base64"] = preview
        return with_preview(result)
