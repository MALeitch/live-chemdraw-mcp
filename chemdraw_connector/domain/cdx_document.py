"""ChemDraw binary .cdx parser integration with cdxml_document/canvas pipeline.

This module provides the missing link: it reads a raw .cdx file using the
pure-Python cdx_binary parser, then adapts the output into the SAME
structured shape that cdxml_document.parse_document produces, reusing
canvas.py's classification completely unchanged.
"""
import os
from typing import List, Dict, Any, Optional

from . import cdx_binary, canvas
from .cdxml_snapshot import parse_bounds


# Violation kinds canvas.py derives purely from coordinates. Kept as a named
# constant so the "cannot be determined" path below stays in step with canvas.py
# rather than silently nulling a future non-geometric check.
_GEOMETRY_VIOLATION_KEYS = frozenset({
    "overlapping_structures", "overflowing_box", "off_page",
    "stoichiometry_grid_overlaps",
})

# Map CDX bond order strings to RDKit bond types (matching cdxml_graph)
_RDKIT_BOND_TYPE = None

def _rdkit_bond_type(order):
    global _RDKIT_BOND_TYPE
    if _RDKIT_BOND_TYPE is None:
        from rdkit import Chem
        _RDKIT_BOND_TYPE = {
            1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE,
            3: Chem.BondType.TRIPLE, 4: Chem.BondType.QUADRUPLE,
            15: Chem.BondType.AROMATIC,  # cdxml_graph's "1.5" -> 15
        }
    from rdkit import Chem
    return _RDKIT_BOND_TYPE.get(order, Chem.BondType.SINGLE)


def _compute_formula(graph):
    """(formula, note) -- formula is None if structure has no real atoms
    or contains any dummy/nickname node."""
    if not graph["nodes"]:
        return None, "no atoms"
    if any(not n["is_real_atom"] for n in graph["nodes"]):
        return None, ("contains a contracted/nickname atom -- true formula "
                      "is not derivable from CDXML alone")
    try:
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
        mol = Chem.RWMol()
        idx_of = {}
        for n in graph["nodes"]:
            atom = Chem.Atom(n["element"])
            if n["charge"]:
                atom.SetFormalCharge(n["charge"])
            if n["num_hydrogens"] is not None:
                atom.SetNoImplicit(True)
                atom.SetNumExplicitHs(n["num_hydrogens"])
            idx_of[n["id"]] = mol.AddAtom(atom)
        for b in graph["bonds"]:
            bt = _rdkit_bond_type(b["order"])
            mol.AddBond(idx_of[b["begin"]], idx_of[b["end"]], bt)
            if bt.name == "AROMATIC":
                mol.GetAtomWithIdx(idx_of[b["begin"]]).SetIsAromatic(True)
                mol.GetAtomWithIdx(idx_of[b["end"]]).SetIsAromatic(True)
        m = mol.GetMol()
        Chem.SanitizeMol(m)
        return rdMolDescriptors.CalcMolFormula(m), None
    except Exception as exc:
        return None, f"formula computation failed: {exc}"


def _fragment_to_unit(frag, native_id_offset=0):
    """Convert a CDX fragment (from cdx_binary.parse) into a unit entry
    matching cdxml_document._parse_structure's output shape."""
    # Build graph from fragment
    graph = {"nodes": frag["nodes"], "bonds": frag["bonds"]}
    
    # Compute formula
    formula, formula_note = _compute_formula(graph)
    
    # Atom/bond counts
    atom_count = len(frag["nodes"])
    bond_count = len(frag["bonds"])
    
    # Generate stable ID
    unit_id = f"cdx-{native_id_offset}"

    # CDX does carry geometry -- kCDXProp_2DPosition on each node -- so derive
    # the box from the atoms rather than reporting None. Nodes without a
    # position are skipped; if none has one, bounds stays None and
    # parse_cdx_document nulls the geometric violations rather than reporting
    # them as clean. Note this is an ATOM-CENTRE box: it excludes label text
    # extents, so it is slightly tighter than ChemDraw's own BoundingBox.
    xs = [n["pos"][0] for n in frag["nodes"] if n.get("pos")]
    ys = [n["pos"][1] for n in frag["nodes"] if n.get("pos")]
    bounds = None
    if xs and ys:
        bounds = {"left": min(xs), "top": min(ys),
                  "right": max(xs), "bottom": max(ys)}


    return {
        "id": unit_id,
        "formula": formula,
        "formula_note": formula_note,
        "atom_count": atom_count,
        "bond_count": bond_count,
        "bounds": bounds,
    }, native_id_offset


def parse_cdx_document(cdx_path: str) -> Dict[str, Any]:
    """Parse a .cdx file into the same structured shape as
    cdxml_document.parse_document (minus reactions/arrows/brackets which
    don't exist in CDX).

    Returns: {
        "structures": [...],
        "captions": [],
        "boxes": [],
        "non_structure_units": [],
        "violations": {...},
        "page_bounds": None,
        "reactions": [],
        "arrows": [],
        "brackets": [],
        "extra_pages": 0
    }
    """
    # Parse CDX into fragments
    fragments = cdx_binary.parse_file(cdx_path)
    
    # Convert each fragment to a unit
    units = []
    struct_native = {}
    native_id = 0
    for frag in fragments:
        entry, nid = _fragment_to_unit(frag, native_id)
        units.append(entry)
        struct_native[nid] = entry["id"]
        native_id += 1
    
    # No captions, arrows, brackets, or schemes in raw CDX
    captions = []
    arrows = []
    boxes = []
    scheme_elems = []
    
    # Use canvas.py to classify and find violations (same as cdxml_document)
    result = canvas.build_canvas(units, captions, boxes,
                                 page_width=None, page_height=None)

    # Every violation canvas.py reports is geometric -- "do these two overlap",
    # "does this sit outside the page". With no bounds to compare, canvas.py
    # finds nothing and returns empty lists, which is indistinguishable from a
    # genuinely clean page. ROADMAP #7 tells callers to always check
    # violations.off_page before trusting a document, so an empty list here is
    # not a harmless default -- it is a false negative on the exact field
    # callers were told to rely on. Report None (cannot be determined) instead.
    if not any(u.get("bounds") for u in units):
        result["violations"] = {
            key: (None if key in _GEOMETRY_VIOLATION_KEYS else value)
            for key, value in result.get("violations", {}).items()
        }
        result["violations_note"] = (
            "geometry checks were not run: no structure on this page carries "
            "coordinates, so overlap/off-page/box-overflow cannot be "
            "determined. These keys are null rather than empty to avoid "
            "reading as 'no problems found'."
        )

    # Add CDX-specific fields
    result["reactions"] = []
    result["arrows"] = arrows
    result["brackets"] = []
    result["extra_pages"] = 0

    return result