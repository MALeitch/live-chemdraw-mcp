"""Reaction scheme tools."""
import json

from ._common import with_preview


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_make_reaction_scheme(reactants_json: str, products_json: str,
                                      reagents_text: str = "",
                                      format: str = "smiles"):
        """Draw a reaction scheme: reactants + arrow (reagents/conditions text
        above it) + products, laid out left to right.
        reactants_json/products_json: JSON lists of structures in the given
        format, e.g. ["CC(=O)Cl", "c1ccccc1O"]. Includes a preview image —
        look at it and adjust if the layout needs work."""
        return with_preview(bridge.make_reaction_scheme(
            json.loads(reactants_json), json.loads(products_json),
            reagents_text or None, format))
