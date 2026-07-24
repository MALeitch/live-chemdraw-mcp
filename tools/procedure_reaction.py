"""Composite reaction-scheme-plus-verification tool.

Bundles chemdraw_make_reaction_scheme with the QC calls a careful user
would always make right afterward, so Claude doesn't have to decide (and
remember) to run them as separate follow-up tool calls."""
from ._common import with_preview


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_make_reaction_scheme_verified(
        reactants: list[str], products: list[str],
        reagents_text: str = "",
        format: str = "smiles",
        conditions_text: str = ""):
        """Draw a reaction scheme AND auto-verify it in one call: this is
        chemdraw_make_reaction_scheme immediately followed by
        chemdraw_check_warnings (scoped to just the structures this call
        drew) and chemdraw_find_duplicates (scoped to the whole document)
        — you do NOT need to make either of those calls yourself
        afterward, they are already included in this result.

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
        before moving on."""
        result = bridge.make_reaction_scheme(
            reactants, products, reagents_text or None, format,
            conditions_text or None)

        object_ids = result.get("object_ids") or []
        result["warnings"] = bridge.check_warnings(object_ids)
        result["duplicate_groups"] = bridge.find_duplicates("document")

        return with_preview(result)
