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
    """Parse CDXML into {"nodes": [...], "bonds": [...]}.

    nodes: {"id", "element", "charge", "is_real_atom", "node_type"}
    bonds: {"begin", "end", "order"}  (order 15 == aromatic-drawn "1.5")

    Only top-level nodes are collected: the inner <fragment> of a contracted
    nickname node is intentionally skipped, so a nickname is one dummy vertex.
    """
    root = ET.fromstring(cdxml_text)
    nodes, bonds = [], []
    _collect(root, nodes, bonds)
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
    return {
        "id": int(n.get("id")),
        "element": int(n.get("Element", "6")),
        "charge": int(float(n.get("Charge", "0"))),
        "node_type": node_type or "Element",
        "is_real_atom": node_type in _REAL_ATOM_NODETYPES,
    }


def _parse_bond(b):
    begin, end = b.get("B"), b.get("E")
    if begin is None or end is None:
        return None
    return {
        "begin": int(begin),
        "end": int(end),
        "order": _ORDER_MAP.get(b.get("Order", "1"), 1),
    }
