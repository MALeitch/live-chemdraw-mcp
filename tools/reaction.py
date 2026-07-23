"""Reaction scheme tools."""
from ._common import with_preview


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_make_reaction_scheme(reactants: list[str], products: list[str],
                                      reagents_text: str = "",
                                      format: str = "smiles",
                                      conditions_text: str = ""):
        """Draw a reaction scheme: reactants + arrow + products, laid out
        left to right.

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
        look at it and adjust if the layout needs work. ALWAYS also check
        `violations.overlapping`: every structure, the arrow, and every
        caption are checked pairwise after layout, so any collision is
        reported here rather than only visible in the preview image. Also
        check `violations.mislaid_captions`: a list of any caption/arrow
        label whose position was verified after placement and didn't land
        where intended even after one retry — should be empty in normal
        use; if it isn't, look at the preview image before trusting the
        layout. Also check `violations.off_page`: a wide scheme (many
        reactants/products, or a long reagents/conditions string) can
        extend past the document's actual page even though it renders
        cleanly in the preview image, which auto-crops to whatever was
        drawn rather than the page boundary."""
        return with_preview(bridge.make_reaction_scheme(
            reactants, products, reagents_text or None, format,
            conditions_text or None))
