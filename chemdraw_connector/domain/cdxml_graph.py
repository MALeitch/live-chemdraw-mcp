"""CDXML -> molecular graph, for substructure work.

CDXML is the one export format whose node ids are the SAME ids the COM layer
reports as Atom.ID (probed live on ChemDraw 26), and the only one that keeps
contracted nicknames (Ph, TES, Boc...) as single nodes instead of silently
expanding them the way molfile export does. So substructure matching runs on
a graph parsed from CDXML, and matched node ids can drive COM selection
directly — no coordinate heuristics.

Zero COM imports; pure XML.
"""
import xml.etree.ElementTree as ET

# CDXML NodeType values that behave as one plain atom of a real element.
# Everything else (Nickname, Fragment, GenericNickname, ElementList,
# ExternalConnectionPoint...) becomes a dummy vertex: bonds to it count for
# connectivity, but no SMARTS element pattern can match it.
_REAL_ATOM_NODETYPES = {None, "", "Element", "Unspecified"}

_ORDER_MAP = {"1": 1, "2": 2, "3": 3, "4": 4, "0.5": 1, "1.5": 15, "2.5": 2}


def parse(cdxml_text):
    """Parse CDXML text into {"nodes": [...], "bonds": [...]}. See
    parse_element for the actual walk -- this just parses text first."""
    return parse_element(ET.fromstring(cdxml_text))


def parse_element(elem):
    """Same as parse(), but starting from an already-parsed ElementTree
    Element instead of raw text -- lets a caller run this on just ONE
    <fragment>/<group> subtree of a larger already-parsed document (e.g.
    domain/cdxml_document.py, which parses a whole page's worth of
    structures without re-serializing each one back to a string first).

    nodes: {"id", "element", "charge", "is_real_atom", "node_type",
            "num_hydrogens"}
    bonds: {"begin", "end", "order"}  (order 15 == aromatic-drawn "1.5")

    num_hydrogens is ChemDraw's own explicit hydrogen count for this atom
    (int) when the CDXML node carries a NumHydrogens attribute, else None
    (no assertion either way -- a caller filling implicit valence itself,
    e.g. RDKit sanitization, should do so exactly as if the attribute were
    never mentioned). See _compute_formula in cdxml_document.py for why
    this can't be treated as "0 means no hydrogens": ChemDraw writes
    NumHydrogens="0" on plenty of ordinary atoms (any heteroatom whose
    bonds already fill its valence) but also uses it to assert a
    non-default count -- most importantly on a radical/open-shell atom,
    where RDKit's own default-valence fill would silently produce a
    different, wrong, but chemically plausible closed-shell formula.

    Only top-level nodes are collected: the inner <fragment> of a contracted
    nickname node is intentionally skipped, so a nickname is one dummy vertex.
    """
    nodes, bonds = [], []
    _collect(elem, nodes, bonds)
    known = {n["id"] for n in nodes}
    # A bond may reference an id we skipped (shouldn't happen at top level,
    # but a malformed page must not crash matching).
    bonds = [b for b in bonds if b["begin"] in known and b["end"] in known]
    return {"nodes": nodes, "bonds": bonds}


def _collect(elem, nodes, bonds):
    for child in elem:
        if child.tag == "n":
            nodes.append(_parse_node(child))
            # do NOT descend: children hold a nickname's internal fragment
        elif child.tag == "b":
            bond = _parse_bond(child)
            if bond is not None:
                bonds.append(bond)
        else:
            _collect(child, nodes, bonds)


def _parse_node(n):
    node_type = n.get("NodeType")
    num_h_str = n.get("NumHydrogens")
    try:
        num_hydrogens = int(num_h_str) if num_h_str is not None else None
    except ValueError:
        num_hydrogens = None  # malformed attribute -- treat as unasserted
    return {
        "id": int(n.get("id")),
        "element": int(n.get("Element", "6")),
        "charge": int(float(n.get("Charge", "0"))),
        "node_type": node_type or "Element",
        "is_real_atom": node_type in _REAL_ATOM_NODETYPES,
        "num_hydrogens": num_hydrogens,
    }


def _parse_bond(b):
    begin, end = b.get("B"), b.get("E")
    if begin is None or end is None:
        return None
    return {
        "begin": int(begin),
        "end": int(end),
        "order": _ORDER_MAP.get(b.get("Order", "1"), 1),
        # Stereochemistry: wedge/hash and friends. Passed through verbatim --
        # CDXML's own Display strings ARE the vocabulary, and domain/
        # cdx_binary.BOND_DISPLAY_MAP maps the binary .cdx integer codes onto
        # exactly these names so both front ends agree. Do not re-encode.
        #
        # None means the bond carried no Display attribute (an ordinary line),
        # which is deliberately distinct from an explicit "Solid".
        #
        # Begin/End in these names is DIRECTIONAL -- it says which atom the
        # narrow point of the wedge sits on, so WedgeBegin and WedgeEnd
        # describe opposite stereocentres. Anything reordering bond endpoints
        # has to carry this along or it silently inverts the chemistry.
        "display": b.get("Display"),
    }
