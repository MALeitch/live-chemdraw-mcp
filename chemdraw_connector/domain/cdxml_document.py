"""CDXML -> semantic document JSON, entirely offline (no COM, no live
ChemDraw). ROADMAP #7.

Reads an arbitrary .cdxml file's structures/captions/arrows/brackets/
reactions into the SAME plain-dict shape state.build_snapshot/
canvas.build_canvas already produce for a live document, reusing
canvas.py's classification (wrapper-duplicate collapsing) and caption-
association logic completely unchanged -- once CDXML is adapted into that
shape, the whole overlap/off-page/box-membership/caption-ownership layer
is free, already tested, and already proven correct against real live
documents.

Structures drawn via ChemDraw's own reaction-scheme tooling
(<scheme><step ReactionStepReactants="10 17" ReactionStepProducts="25"
ReactionStepArrows="51" .../></scheme> -- confirmed live from this
connector's own chemdraw_make_reaction_scheme output) are reported under
`reactions`, resolved by native CDX id, not spatial guessing. A loose
hand-drawn arrow with no <scheme> wrapper is still parsed as a structure/
arrow, just not grouped into a reaction -- no spatial "arrow position vs
structure position" fallback heuristic (see ROADMAP.md #7's notes).

Formula is computed by building an RDKit RWMol from each structure's
parsed atom/bond graph and letting RDKit's own sanitization handle
implicit hydrogens/aromaticity -- NOT a hand-rolled Hill counter. Verified
live (2026-07-27) that ChemDraw only writes an explicit NumHydrogens
attribute on SOME atoms (ones it needed to clean up), never universally,
so a raw atom-only count would silently be wrong for any atom relying on
implicit valence (i.e. most plain carbons) -- RDKit's sanitizer is the
correct way to derive this, the same way domain/enumeration.py already
leans on RDKit rather than reimplementing valence rules. A structure
containing any dummy/nickname atom (a contracted label like "Ph"/"Boc")
gets formula=None with a note instead of a guess -- true formula for a
nickname lives in ChemDraw's own database, not the CDXML export (same
documented limitation as domain/cdxml_snapshot.py / state.build_snapshot).

Only .cdxml is supported here -- a .cdx file must be converted first (see
tools/offline_parse.py, which points callers at chemdraw_convert_cdx_cdxml).

Zero COM imports; pure XML + the existing pure canvas/cdxml_graph/
cdxml_snapshot modules + RDKit (already a hard dependency of this repo).
"""
import xml.etree.ElementTree as ET

from . import canvas, cdxml_graph
from .cdxml_snapshot import parse_bounds

_BOND_ORDER_TO_RDKIT = None  # lazy -- see _rdkit_bond_type, avoids importing
                              # rdkit at module load for callers that only
                              # ever hit documents with dummy/nickname atoms


def _rdkit_bond_type(order):
    global _BOND_ORDER_TO_RDKIT
    if _BOND_ORDER_TO_RDKIT is None:
        from rdkit import Chem
        _BOND_ORDER_TO_RDKIT = {
            1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE,
            3: Chem.BondType.TRIPLE, 4: Chem.BondType.QUADRUPLE,
            15: Chem.BondType.AROMATIC,  # cdxml_graph's own "1.5" -> 15 mapping
        }
    from rdkit import Chem
    return _BOND_ORDER_TO_RDKIT.get(order, Chem.BondType.SINGLE)


def _compute_formula(graph):
    """(formula, note) -- formula is None (with an explanatory note) if the
    structure has no real atoms or contains any dummy/nickname node."""
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


def _walk(elem, structures, captions, arrows, graphics, schemes):
    """Single recursive pass collecting every page-level object.
    <fragment> is a leaf (its own atom-label <t>/nested content is never a
    separate object) -- never descended into. <group> is collected AS a
    structure candidate itself (may turn out to be a wrapper -- see module
    docstring) AND descended into, since group nesting is exactly how a
    multi-fragment insert (a salt) or a caption grouped with its structure
    is represented -- both need their own inner <fragment>s collected too.
    A <t> is only ever reached here for something NOT inside a <fragment>
    (fragments are leaves), so every collected <t> is a real caption, never
    an atom label."""
    for child in elem:
        tag = child.tag
        if tag == "fragment":
            structures.append(child)
        elif tag == "group":
            structures.append(child)
            _walk(child, structures, captions, arrows, graphics, schemes)
        elif tag == "t":
            captions.append(child)
        elif tag == "arrow":
            arrows.append(child)
        elif tag == "graphic":
            graphics.append(child)
        elif tag == "scheme":
            schemes.append(child)
        elif tag in ("colortable", "fonttable", "objecttag"):
            continue
        else:
            _walk(child, structures, captions, arrows, graphics, schemes)


def _parse_structure(elem):
    native_id = elem.get("id")
    graph = cdxml_graph.parse_element(elem)
    # ALL top-level nodes, not just is_real_atom ones -- that distinction
    # is for substructure SMARTS matching (a dummy/nickname vertex can't
    # match a specific element pattern), not counting. A contracted
    # nickname (e.g. "Ph") is exactly ONE dummy node but COM's own
    # Atoms.Count still reports 1 for it (confirmed live, cross-checked
    # against chemdraw_get_document_state on a real contracted structure)
    # -- same "one count per <n>, whether real or dummy" convention
    # domain/cdxml_snapshot.py's _count_visible already established.
    # Filtering this to real-only atoms undercounted a nickname structure
    # to 0, which then got it wrongly excluded as a "no atoms" decoration
    # group instead of a real structure.
    atom_count = len(graph["nodes"])
    bond_count = len(graph["bonds"])
    bounds_str = elem.get("BoundingBox")
    formula, formula_note = _compute_formula(graph)
    return {
        "id": f"cdx-{native_id}",
        "formula": formula,
        "formula_note": formula_note,
        "atom_count": atom_count,
        "bond_count": bond_count,
        "bounds": parse_bounds(bounds_str) if bounds_str else None,
    }, native_id


def _caption_text(t_elem):
    """Concatenate every <s> run's text -- a subscript-formatted caption
    (e.g. "K2CO3") is multiple runs, same structure domain/reagent_text.py
    writes on the live-insert side."""
    return "".join(s.text or "" for s in t_elem.iter("s"))


def _parse_caption(t_elem, group_of):
    native_id = t_elem.get("id")
    bounds_str = t_elem.get("BoundingBox")
    tag_owner_id = None
    for ot in t_elem.findall("objecttag"):
        if ot.get("Name") == "claude_caption_owner":
            # A non-sentinel value here is a claude-... id from whatever
            # LIVE session originally created this file -- meaningless in
            # THIS offline parse's own cdx-... id space, so it's dropped
            # (canvas.associate_captions falls through to its spatial
            # tiers when tag_owner_id doesn't resolve, which is correct
            # for a foreign id anyway). The sentinel itself (marking a
            # reaction scheme's own reagents/"+"/arrow-fallback captions
            # as deliberately unowned) is real, connector-independent
            # signal and IS honored.
            val = ot.get("Value")
            tag_owner_id = val if val == canvas.NO_CAPTION_OWNER_SENTINEL else None
            break
    return {
        "id": f"cdx-{native_id}",
        "text": _caption_text(t_elem),
        "bounds": parse_bounds(bounds_str) if bounds_str else None,
        "group_id": group_of.get(id(t_elem)),
        "tag_owner_id": tag_owner_id,
    }, native_id


def _parse_arrow(elem):
    native_id = elem.get("id")
    bounds_str = elem.get("BoundingBox")
    return {
        "id": f"cdx-{native_id}",
        "bounds": parse_bounds(bounds_str) if bounds_str else None,
    }, native_id


def _resolve_ids(native_id_str, id_map):
    """A step's space-separated native-id-reference string -> (resolved
    our-ids, unresolved native ids). Never silently drops a reference that
    doesn't map to anything collected -- e.g. a <step> referencing a
    decoration graphic this parser doesn't track as its own object."""
    if not native_id_str:
        return [], []
    resolved, unresolved = [], []
    for nid in native_id_str.split():
        if nid in id_map:
            resolved.append(id_map[nid])
        else:
            unresolved.append(nid)
    return resolved, unresolved


def parse_document(cdxml_text):
    """The one entry point: CDXML text -> the same structured shape
    canvas.build_canvas already returns for a live document
    (structures/captions/boxes/non_structure_units/violations/
    page_bounds), plus `reactions` and `arrows`/`brackets` (native-only
    concepts with no live-path equivalent to reuse)."""
    root = ET.fromstring(cdxml_text)
    page = root.find(".//page")
    if page is None:
        return {
            "structures": [], "captions": [], "boxes": [],
            "non_structure_units": [], "violations": {}, "page_bounds": None,
            "reactions": [], "arrows": [], "brackets": [],
        }

    page_width = page_height = None
    page_box = page.get("BoundingBox")
    if page_box:
        b = parse_bounds(page_box)
        page_width, page_height = b["right"], b["bottom"]

    structure_elems, caption_elems = [], []
    arrow_elems, graphic_elems, scheme_elems = [], [], []
    _walk(page, structure_elems, caption_elems, arrow_elems, graphic_elems,
         scheme_elems)

    # Map each <t> to its immediate parent <group>'s python id (if any) --
    # used for group_id below, mirroring canvas.associate_captions' tier-1
    # "grouped directly with its structure" case (the one path a caption
    # grouped BY HAND in ChemDraw's own UI, as opposed to this connector's
    # own claude_caption_owner tag, actually produces).
    group_of_child = {}
    for group in page.iter("group"):
        for child in group:
            if child.tag == "t":
                group_of_child[id(child)] = f"cdx-{group.get('id')}"

    units, struct_native = [], {}
    for elem in structure_elems:
        entry, native_id = _parse_structure(elem)
        units.append(entry)
        struct_native[native_id] = entry["id"]

    captions, caption_native = [], {}
    for elem in caption_elems:
        entry, native_id = _parse_caption(elem, group_of_child)
        captions.append(entry)
        caption_native[native_id] = entry["id"]

    arrows, arrow_native = [], {}
    for elem in arrow_elems:
        entry, native_id = _parse_arrow(elem)
        arrows.append(entry)
        arrow_native[native_id] = entry["id"]

    boxes, brackets = [], []
    box_index = 0
    for elem in graphic_elems:
        if elem.get("SupersededBy"):
            # Confirmed live: ChemDraw writes a legacy/compatibility
            # <graphic GraphicType="Line" ArrowType="FullHead"> alongside
            # every real <arrow> it also exports, explicitly marked
            # SupersededBy="<the real arrow's id>" -- the real object is
            # the <arrow> element (already collected separately above),
            # this is just a fallback for older CDX readers. Without this
            # check it was silently picked up as a fake zero-height "box"
            # sitting exactly on top of the real arrow.
            continue
        bounds_str = elem.get("BoundingBox")
        bounds = parse_bounds(bounds_str) if bounds_str else None
        if elem.get("GraphicType") == "Bracket":
            # Brackets are naturally thin (a "[" glyph's own bounding box
            # is legitimately zero-width) -- must NOT go through the
            # degenerate-bounds filter below, that's only meaningful for
            # the generic box fallback.
            brackets.append({
                "id": f"cdx-{elem.get('id')}",
                "bracket_type": elem.get("BracketType"),
                "bracket_usage": elem.get("BracketUsage"),
                "bounds": bounds,
            })
            continue
        if bounds is not None and (bounds["right"] == bounds["left"]
                                   or bounds["bottom"] == bounds["top"]):
            continue  # degenerate (zero width or height) -- not a real box
        if bounds is not None:
            # Not confirmed live which GraphicType value a hand-drawn
            # panel-box rectangle uses (no live tool in this connector
            # creates one to test against, unlike brackets/arrows) --
            # everything that isn't a Bracket is treated as a candidate
            # box for now. See ROADMAP.md #7's notes.
            box_index += 1
            boxes.append({"index": box_index, "bounds": bounds})

    result = canvas.build_canvas(units, captions, boxes,
                                 page_width=page_width, page_height=page_height)

    reactions = []
    for scheme in scheme_elems:
        for step in scheme.findall("step"):
            reactant_ids, unresolved_r = _resolve_ids(
                step.get("ReactionStepReactants"), struct_native)
            product_ids, unresolved_p = _resolve_ids(
                step.get("ReactionStepProducts"), struct_native)
            arrow_ids, unresolved_a = _resolve_ids(
                step.get("ReactionStepArrows"), arrow_native)
            above_ids, _ = _resolve_ids(
                step.get("ReactionStepObjectsAboveArrow"), caption_native)
            below_ids, _ = _resolve_ids(
                step.get("ReactionStepObjectsBelowArrow"), caption_native)
            text_by_id = {c["id"]: c["text"] for c in captions}
            reactions.append({
                "reactant_ids": reactant_ids,
                "product_ids": product_ids,
                "arrow_ids": arrow_ids,
                "reagents_text": " ".join(text_by_id[i] for i in above_ids if i in text_by_id),
                "conditions_text": " ".join(text_by_id[i] for i in below_ids if i in text_by_id),
                "unresolved_ids": unresolved_r + unresolved_p + unresolved_a,
            })

    result["reactions"] = reactions
    result["arrows"] = arrows
    result["brackets"] = brackets
    return result
