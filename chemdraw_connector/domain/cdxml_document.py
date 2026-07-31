"""CDXML -> semantic document JSON, entirely offline (no COM, no live
ChemDraw).

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
ReactionStepArrows="48" .../></scheme> -- ReactionStepArrows points at
the legacy SupersededBy graphic's own id, not the real <arrow>'s id (see
the SupersededBy-alias handling below); confirmed live from this
connector's own chemdraw_make_reaction_scheme output) are reported under
`reactions`, resolved by native CDX id, not spatial guessing. A loose
hand-drawn arrow with no <scheme> wrapper is still parsed as a
structure/arrow, just not grouped into a reaction -- no spatial "arrow
position vs structure position" fallback heuristic.

CONFIRMED LIVE that this does NOT mean every `reactions` entry describes
a real reaction: ChemDraw can ALSO wrap
a completely unrelated loose arrow (e.g. one made with
chemdraw_make_arrow, positioned nowhere near the structure it ends up
paired with) in its own native <scheme><step>, unprompted. `reactions`
faithfully reflects ChemDraw's OWN <scheme> interpretation -- this
parser is not guessing, ChemDraw itself is. A reaction step with empty
`product_ids` (an arrow with no product at all, unusual for a real
single-step scheme) is a signal to treat that entry as low-confidence
rather than a genuine reaction.

Formula is computed by building an RDKit RWMol from each structure's
parsed atom/bond graph. Most atoms have no NumHydrogens attribute in the
CDXML at all -- RDKit's own sanitizer fills their implicit hydrogens from
default valence, NOT a hand-rolled Hill counter, the same way
domain/enumeration.py already leans on RDKit rather than reimplementing
valence rules. But when a node DOES carry a NumHydrogens attribute
(cdxml_graph.parse's num_hydrogens field), that count is taken as
authoritative and stamped onto the RDKit atom via SetNoImplicit +
SetNumExplicitHs instead of trusting RDKit's own valence-based fill.

CONFIRMED LIVE (2026-07-30) that this distinction is load-bearing, not
cosmetic: a benzyl radical ([CH2]c1ccccc1) exports with
NumHydrogens="2" on its radical carbon (ChemDraw's own true count) plus
Warning="An atom in this label has an invalid valence." -- RDKit's
default-valence fill for that same carbon (0 charge, 1 explicit bond) is
3, giving toluene's formula (C7H8) for a benzyl radical (COM's own
ChemDraw_get_properties on the identical structure: C7H7) with no
signal that anything was wrong. Charged species (e.g. the benzyl anion,
NumHydrogens="2" Charge="-1") happened to still compute correctly under
pure RDKit inference -- because RDKit's own valence model already accounts
for formal charge -- which is why this gap survived this codebase's
existing charged-species test coverage; the radical case has no such
lucky coincidence, since RDKit has no way to know a neutral atom carries
an unpaired electron rather than a full default-valence complement of
implicit hydrogens. A structure containing any dummy/nickname atom (a
contracted label like "Ph"/"Boc") gets formula=None with a note instead of
a guess -- true formula for a nickname lives in ChemDraw's own database,
not the CDXML export (same documented limitation as
domain/cdxml_snapshot.py / state.build_snapshot).

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
            if n["num_hydrogens"] is not None:
                # ChemDraw's own asserted H count -- do NOT let RDKit's
                # default-valence sanitizer override it (see module
                # docstring: this is what a radical/open-shell atom needs).
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
    concepts with no live-path equivalent to reuse).

    Walks EVERY <page> element, not just the first: content on a
    second/subsequent <page> used to be silently dropped. Reachability
    of a genuinely multi-<page> CDXML export was NOT confirmed -- every
    real ChemDraw export checked (~40 real connector backups including
    genuine user documents) has exactly one <page>, expressing extent via
    HeightPages/WidthPages tiling attributes on that single page rather
    than sibling <page> elements. `page_bounds`/`violations.off_page`
    still come from the FIRST page only -- canvas.build_canvas has no
    multi-page concept, and there is no real multi-page export to
    validate a design against, so this doesn't guess at one.
    `extra_pages` reports how many additional <page> elements were found
    beyond the first, so a caller can tell when this limitation might
    actually matter, instead of the previous silent content loss."""
    root = ET.fromstring(cdxml_text)
    pages = root.findall(".//page")
    if not pages:
        return {
            "structures": [], "captions": [], "boxes": [],
            "non_structure_units": [], "violations": {}, "page_bounds": None,
            "reactions": [], "arrows": [], "brackets": [],
        }

    page_width = page_height = None
    page_box = pages[0].get("BoundingBox")
    if page_box:
        b = parse_bounds(page_box)
        page_width, page_height = b["right"], b["bottom"]

    structure_elems, caption_elems = [], []
    arrow_elems, graphic_elems, scheme_elems = [], [], []
    # Map each <t> to its immediate parent <group>'s python id (if any) --
    # used for group_id below, mirroring canvas.associate_captions' tier-1
    # "grouped directly with its structure" case (the one path a caption
    # grouped BY HAND in ChemDraw's own UI, as opposed to this connector's
    # own claude_caption_owner tag, actually produces).
    group_of_child = {}
    for page in pages:
        _walk(page, structure_elems, caption_elems, arrow_elems, graphic_elems,
             scheme_elems)
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
        superseded_by = elem.get("SupersededBy")
        if superseded_by:
            # Confirmed live: ChemDraw writes a legacy/compatibility
            # <graphic GraphicType="Line" ArrowType="FullHead"> alongside
            # every real <arrow> it also exports, explicitly marked
            # SupersededBy="<the real arrow's id>" -- the real object is
            # the <arrow> element (already collected separately above),
            # this is just a fallback for older CDX readers. Without this
            # check it was silently picked up as a fake zero-height "box"
            # sitting exactly on top of the real arrow.
            #
            # CONFIRMED LIVE: a <scheme><step ReactionStepArrows="...">
            # reference points at THIS legacy graphic's own id, not the
            # real <arrow>'s id --
            # so without an alias, every reaction's arrow_ids came back
            # empty and the id landed in unresolved_ids on every healthy
            # file. Alias this graphic's native id to whatever our-id the
            # real arrow (already collected above, arrow loop runs first)
            # resolved to, so a step reference through either id resolves
            # to the same arrow. Falls back to leaving it unresolved (same
            # as before this fix) if the target arrow id isn't found --
            # never crashes on a malformed/unexpected document.
            real_arrow_id = arrow_native.get(superseded_by)
            graphic_native_id = elem.get("id")
            if real_arrow_id is not None and graphic_native_id is not None:
                arrow_native[graphic_native_id] = real_arrow_id
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
            # box for now.
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
    if len(pages) > 1:
        # See this function's own docstring: content from every page is
        # now included, but page_bounds/off_page violations reflect only
        # the first page's extent. Surface the count so a caller isn't
        # silently trusting off_page for structures actually on page 2+.
        result["extra_pages"] = len(pages) - 1
    return result
