"""Reaction scheme tools."""
from ._common import parse_json_arg, with_preview


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_make_reaction_scheme(reactants_json: str, products_json: str,
                                      reagents_text: str = "",
                                      format: str = "smiles"):
        """Draw a reaction scheme: reactants + arrow (reagents/conditions text
        above it) + products, laid out left to right.
        reactants_json/products_json: JSON lists of structures in the given
        format, e.g. ["CC(=O)Cl", "c1ccccc1O"]. Includes a preview image —
        look at it and adjust if the layout needs work. ALWAYS also check
        `violations.overlapping`: every structure, the arrow, and every
        caption are checked pairwise after layout, so any collision is
        reported here rather than only visible in the preview image."""
        return with_preview(bridge.make_reaction_scheme(
            parse_json_arg(reactants_json, "reactants_json", list),
            parse_json_arg(products_json, "products_json", list),
            reagents_text or None, format))
