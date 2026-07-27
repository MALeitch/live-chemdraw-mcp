"""Offline systematic IUPAC name resolution tools (OPSIN-based)."""
from chemdraw_connector.domain import naming

from ._common import as_json


def register(mcp, bridge):
    @mcp.tool(description=(
        "Resolve a SYSTEMATIC IUPAC chemical name (e.g. '2-acetoxybenzoic "
        "acid', 'ethanol', 'benzene') to SMILES/InChI/InChIKey/formula/"
        "exact_mass, entirely offline via a bundled OPSIN + JRE (downloaded "
        "on first use to %LOCALAPPDATA%\\chemdraw-mcp\\opsin, then cached "
        "locally in a sqlite db keyed by name). OPSIN does NOT understand "
        "trivial/common names ('aspirin', 'caffeine', 'Tylenol') -- this "
        "raises a clear error for those; fall back to "
        "chemdraw_insert_structure(representation=name, format='name'), "
        "which queries PubChem online for common names instead."))
    def chemdraw_resolve_systematic_name(name: str) -> str:
        return as_json(naming.resolve_name_offline(name))
