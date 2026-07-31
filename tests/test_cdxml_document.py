"""domain/cdxml_document.py -- offline CDXML -> semantic JSON.
Pure, no COM. Fixtures below are compact hand-built snippets, but
every tag/attribute shape they use (native <arrow>, <scheme><step
ReactionStepReactants=...>, <graphic GraphicType="Bracket">, a legacy
<graphic SupersededBy="..."> duplicate of the real arrow, a caption's
Warning="Chemical Interpretation is not possible for this label") was
confirmed live by exporting real ChemDraw documents via
chemdraw_export_cdxml and inspecting the raw XML directly -- not
guessed."""
from chemdraw_connector.domain import cdxml_document

# One reaction step: acetyl chloride (id 10) + phenol (id 17) -> phenyl
# acetate (id 25), with a real <arrow> (id 51) and reagents/conditions
# captions (ids 48/49) referenced by the <scheme><step> -- same shape as
# chemdraw_make_reaction_scheme's own live CDXML export. Also includes the
# legacy <graphic id="52" SupersededBy="51" GraphicType="Line"> ChemDraw
# writes alongside every real <arrow>, to confirm it's correctly excluded
# from `boxes` rather than picked up as a fake box.
#
# ReactionStepArrows="52" (the GRAPHIC's own id), not "51" (the real
# arrow's id) -- confirmed live that ChemDraw's own <scheme><step>
# element cross-references the legacy compatibility graphic, not the
# <arrow> it supersedes. An earlier version of this fixture had this
# backwards (wired to "51", the sensible-looking assumption) and its own
# test asserted the resulting false resolution as correct -- a hand-built
# fixture encoding the author's assumption rather than reality.
REACTION_CDXML = """<?xml version="1.0" ?>
<CDXML>
 <page id="1" BoundingBox="0 0 540 720">
  <fragment id="10" BoundingBox="10 10 30 30">
   <n id="1" Element="6"/><n id="2" Element="6"/><n id="3" Element="8"/><n id="4" Element="17"/>
   <b id="1" B="1" E="2" Order="1"/><b id="2" B="2" E="3" Order="2"/><b id="3" B="2" E="4" Order="1"/>
  </fragment>
  <fragment id="17" BoundingBox="60 10 90 30">
   <n id="5" Element="8"/><n id="6" Element="6"/><n id="7" Element="6"/><n id="8" Element="6"/>
   <n id="9" Element="6"/><n id="10" Element="6"/><n id="11" Element="6"/>
   <b id="4" B="5" E="6" Order="1"/><b id="5" B="6" E="7" Order="1.5"/><b id="6" B="7" E="8" Order="1.5"/>
   <b id="7" B="8" E="9" Order="1.5"/><b id="8" B="9" E="10" Order="1.5"/>
   <b id="9" B="10" E="11" Order="1.5"/><b id="10" B="11" E="6" Order="1.5"/>
  </fragment>
  <graphic id="52" SupersededBy="51" BoundingBox="200 20 260 20" GraphicType="Line" ArrowType="FullHead"/>
  <arrow id="51" BoundingBox="200 18 260 22" Head3D="260 20 0" Tail3D="200 20 0"/>
  <t id="48" BoundingBox="210 5 230 15" Warning="Chemical Interpretation is not possible for this label"><s>Et3N</s></t>
  <t id="49" BoundingBox="210 25 240 35" Warning="Chemical Interpretation is not possible for this label"><s>DCM, 0 </s><s face="32">o</s><s>C</s></t>
  <fragment id="25" BoundingBox="280 10 310 30">
   <n id="12" Element="6"/><n id="13" Element="6"/><n id="14" Element="8"/><n id="15" Element="8"/>
   <n id="16" Element="6"/><n id="17" Element="6"/><n id="18" Element="6"/><n id="19" Element="6"/>
   <n id="20" Element="6"/><n id="21" Element="6"/>
   <b id="11" B="12" E="13" Order="1"/><b id="12" B="13" E="14" Order="2"/><b id="13" B="13" E="15" Order="1"/>
   <b id="14" B="15" E="16" Order="1"/><b id="15" B="16" E="17" Order="1.5"/><b id="16" B="17" E="18" Order="1.5"/>
   <b id="17" B="18" E="19" Order="1.5"/><b id="18" B="19" E="20" Order="1.5"/><b id="19" B="20" E="21" Order="1.5"/>
   <b id="20" B="21" E="16" Order="1.5"/>
  </fragment>
  <t id="52b" BoundingBox="290 35 305 45" Warning="Chemical Interpretation is not possible for this label"><s>92%</s></t>
  <scheme id="60"><step id="61" ReactionStepReactants="10 17" ReactionStepProducts="25"
   ReactionStepArrows="52" ReactionStepObjectsAboveArrow="48" ReactionStepObjectsBelowArrow="49"/></scheme>
 </page>
</CDXML>
"""


def test_page_bounds():
    result = cdxml_document.parse_document(REACTION_CDXML)
    assert result["page_bounds"] == {"width": 540.0, "height": 720.0}


def test_structures_parsed_with_correct_formula_and_counts():
    result = cdxml_document.parse_document(REACTION_CDXML)
    by_id = {s["id"]: s for s in result["structures"]}
    assert by_id["cdx-10"]["formula"] == "C2H3ClO"
    assert by_id["cdx-10"]["atom_count"] == 4
    assert by_id["cdx-10"]["bond_count"] == 3
    assert by_id["cdx-17"]["formula"] == "C6H6O"
    assert by_id["cdx-25"]["formula"] == "C8H8O2"


def test_captions_separated_from_atom_labels_and_multi_run_text_joined():
    result = cdxml_document.parse_document(REACTION_CDXML)
    texts = {c["id"]: c["text"] for c in result["captions"]}
    # Exactly the 3 real page-level captions -- none of the <n Element=...>
    # atoms inside the fragments leaked in as captions.
    assert texts == {"cdx-48": "Et3N", "cdx-49": "DCM, 0 oC", "cdx-52b": "92%"}


def test_legacy_superseded_arrow_graphic_excluded_from_boxes():
    result = cdxml_document.parse_document(REACTION_CDXML)
    assert result["boxes"] == []
    assert result["arrows"] == [
        {"id": "cdx-51", "bounds": {"left": 200.0, "top": 18.0, "right": 260.0, "bottom": 22.0}}
    ]


def test_reaction_step_resolves_reactants_products_arrow_and_text():
    # arrow_ids == ["cdx-51"] via the SupersededBy alias, even though the
    # <step> element itself references "52" (the graphic), not "51" (the
    # arrow) -- see the SupersededBy-alias fix in
    # cdxml_document.parse_document.
    result = cdxml_document.parse_document(REACTION_CDXML)
    assert len(result["reactions"]) == 1
    step = result["reactions"][0]
    assert step["reactant_ids"] == ["cdx-10", "cdx-17"]
    assert step["product_ids"] == ["cdx-25"]
    assert step["arrow_ids"] == ["cdx-51"]
    assert step["reagents_text"] == "Et3N"
    assert step["conditions_text"] == "DCM, 0 oC"
    assert step["unresolved_ids"] == []


# Captured VERBATIM from a real ChemDraw 26 export (chemdraw_make_
# reaction_scheme(["CC(=O)Cl","c1ccccc1O"], ["CC(=O)Oc1ccccc1"],
# reagents_text="pyridine", conditions_text="DCM, 0 °C, 2 h") then
# chemdraw_export_cdxml), trimmed to the elements that matter for arrow
# resolution. Independent of REACTION_CDXML above (which was hand-built
# and then corrected once this bug was found) -- this one was never wrong.
REAL_ARROW_ALIAS_CDXML = """<?xml version="1.0" ?>
<CDXML><page id="1" BoundingBox="0 0 540 719.75">
 <fragment id="10" BoundingBox="60 104.92 90.92 135.08"><n id="1" Element="6"/></fragment>
 <fragment id="17" BoundingBox="138.92 103.15 187.75 136.85"><n id="2" Element="6"/></fragment>
 <fragment id="25" BoundingBox="323.10 103.43 385.90 136.57"><n id="3" Element="6"/></fragment>
 <graphic id="48" SupersededBy="51" BoundingBox="299.10 120 211.75 120" Z="48"
  GraphicType="Line" ArrowType="FullHead" HeadSize="1500"/>
 <arrow id="51" BoundingBox="211.75 117.15 299.10 122.25" Z="48"
  Head3D="299.10 120 0" Tail3D="211.75 120 0"/>
 <t id="45" BoundingBox="237.91 87.72 272.93 98.10"><s>pyridine</s></t>
 <t id="46" BoundingBox="221.75 135.54 289.10 145.40"><s>DCM, 0 </s><s face="32">o</s><s>C, 2 h</s></t>
 <scheme id="52"><step id="53" ReactionStepReactants="10 17" ReactionStepProducts="25"
  ReactionStepArrows="48" ReactionStepObjectsAboveArrow="45" ReactionStepObjectsBelowArrow="46"/></scheme>
</page></CDXML>
"""


def test_real_capture_arrow_alias_resolves_not_unresolved():
    result = cdxml_document.parse_document(REAL_ARROW_ALIAS_CDXML)
    assert result["arrows"] == [
        {"id": "cdx-51",
         "bounds": {"left": 211.75, "top": 117.15, "right": 299.10, "bottom": 122.25}}
    ]
    step = result["reactions"][0]
    assert step["arrow_ids"] == ["cdx-51"]
    assert step["unresolved_ids"] == []


def test_unresolved_reaction_step_reference_reported_not_dropped():
    cdxml = """<?xml version="1.0" ?>
    <CDXML><page id="1" BoundingBox="0 0 100 100">
     <fragment id="1" BoundingBox="0 0 10 10"><n id="1"/></fragment>
     <scheme id="2"><step id="3" ReactionStepReactants="1 999" ReactionStepProducts="1"/></scheme>
    </page></CDXML>"""
    result = cdxml_document.parse_document(cdxml)
    step = result["reactions"][0]
    assert step["reactant_ids"] == ["cdx-1"]
    assert step["unresolved_ids"] == ["999"]


NICKNAME_CDXML = """<?xml version="1.0" ?>
<CDXML><page id="1" BoundingBox="0 0 100 100">
 <fragment id="30" BoundingBox="0 0 20 10">
  <n id="1"/>
  <n id="2" NodeType="Fragment">
   <fragment id="31"><n id="3"/><n id="4"/><b id="1" B="3" E="4"/></fragment>
   <t><s>Ph</s></t>
  </n>
  <b id="2" B="1" E="2"/>
 </fragment>
</page></CDXML>
"""


def test_nickname_structure_has_no_formula_but_correct_atom_count():
    result = cdxml_document.parse_document(NICKNAME_CDXML)
    assert len(result["structures"]) == 1
    s = result["structures"][0]
    # Matches COM's own Atoms.Count convention (cdxml_snapshot._count_
    # visible) -- one count per <n>, whether real or a dummy nickname
    # node, NOT filtered to real atoms only (that undercounted this to 0
    # in an earlier version, wrongly excluding it as a decoration group).
    assert s["atom_count"] == 2
    assert s["formula"] is None
    assert "nickname" in s["formula_note"]


BRACKET_CDXML = """<?xml version="1.0" ?>
<CDXML><page id="1" BoundingBox="0 0 200 200">
 <fragment id="1" BoundingBox="10 10 20 20"><n id="1"/></fragment>
 <graphic id="2" BoundingBox="5 5 5 25" GraphicType="Bracket" BracketType="Square"/>
 <graphic id="3" BoundingBox="25 5 25 25" GraphicType="Bracket" BracketType="Square" BracketUsage="SRU"/>
</page></CDXML>
"""


def test_bracket_pair_captured_despite_zero_width_bounds():
    result = cdxml_document.parse_document(BRACKET_CDXML)
    assert len(result["brackets"]) == 2
    usages = {b["id"]: b["bracket_usage"] for b in result["brackets"]}
    assert usages == {"cdx-2": None, "cdx-3": "SRU"}
    assert result["boxes"] == []  # thin brackets must not be mistaken for panel boxes


SALT_CDXML = """<?xml version="1.0" ?>
<CDXML><page id="1" BoundingBox="0 0 200 200">
 <group id="5" BoundingBox="0 0 30 10">
  <fragment id="6" BoundingBox="0 0 10 10"><n id="1" Element="1" Charge="-1"/></fragment>
  <fragment id="7" BoundingBox="20 0 30 10"><n id="2" Element="11" Charge="1"/></fragment>
 </group>
</page></CDXML>
"""


# Regression: parse_document used to resolve only the FIRST <page>
# (root.find(".//page")), silently dropping content from any subsequent
# <page>. Reachability of a
# genuinely multi-<page> CDXML export was never confirmed (every real
# export checked has exactly one <page>) -- this fixture is a synthetic
# construction to exercise the parser's own multi-page handling, not a
# live capture, unlike most fixtures in this file.
TWO_PAGE_CDXML = """<?xml version="1.0" ?>
<CDXML>
 <page id="1" BoundingBox="0 0 200 200">
  <fragment id="10" BoundingBox="10 10 30 30">
   <n id="1" Element="6"/><n id="2" Element="8"/><b id="1" B="1" E="2"/>
  </fragment>
 </page>
 <page id="2" BoundingBox="0 0 300 300">
  <fragment id="20" BoundingBox="10 10 30 30">
   <n id="3" Element="6"/><n id="4" Element="7"/><b id="2" B="3" E="4"/>
  </fragment>
 </page>
</CDXML>
"""


def test_multi_page_content_included_not_silently_dropped():
    result = cdxml_document.parse_document(TWO_PAGE_CDXML)
    ids = {s["id"] for s in result["structures"]}
    assert ids == {"cdx-10", "cdx-20"}  # both pages' content present
    assert result["extra_pages"] == 1
    # page_bounds/off_page still reflect only the FIRST page's extent --
    # see parse_document's own docstring on why (no real multi-page
    # export to validate a merged-bounds design against).
    assert result["page_bounds"] == {"width": 200.0, "height": 200.0}


def test_single_page_document_has_no_extra_pages_key():
    result = cdxml_document.parse_document(REACTION_CDXML)
    assert "extra_pages" not in result


def test_ionic_wrapper_group_collapsed_via_canvas_reuse():
    # Same union-wrapper shape find_union_wrapper_duplicates already
    # handles for the live COM path (an insert_structure("[Na+].[H-]")
    # salt) -- confirms the offline adapter feeds domain/canvas.py
    # correctly, reusing its classification unchanged rather than
    # reimplementing it.
    result = cdxml_document.parse_document(SALT_CDXML)
    real_ids = {s["id"] for s in result["structures"]}
    assert real_ids == {"cdx-6", "cdx-7"}
    excluded_ids = {u["id"] for u in result["non_structure_units"]}
    assert excluded_ids == {"cdx-5"}


# Benzyl radical -- attributes captured VERBATIM from a real ChemDraw 26
# export (chemdraw_insert_structure("[CH2]c1ccccc1") then
# chemdraw_export_cdxml), not hand-guessed -- per this file's own module
# docstring rule. Atom 1 is the radical carbon: ChemDraw writes its own
# true hydrogen count (NumHydrogens="2") plus a valence Warning, but does
# NOT zero out any bond -- a naive RDKit sanitize (no charge, 1 explicit
# bond, default carbon valence 4) fills 3 implicit hydrogens instead of 2,
# silently producing toluene's formula (C7H8) for a benzyl radical. COM's
# own chemdraw_get_properties on the identical live structure: C7H7.
RADICAL_CDXML = """<?xml version="1.0" ?>
<CDXML><page id="1" BoundingBox="0 0 540 719.75">
 <fragment id="17" BoundingBox="231.34 217.75 284.06 251.45">
  <n id="1" Warning="An atom in this label has an invalid valence." NumHydrogens="2"/>
  <n id="2"/><n id="3"/><n id="4"/><n id="5"/><n id="6"/><n id="7"/>
  <b id="9" B="1" E="2"/>
  <b id="10" B="2" E="3" Order="2"/>
  <b id="11" B="3" E="4"/>
  <b id="12" B="4" E="5" Order="2"/>
  <b id="13" B="5" E="6"/>
  <b id="14" B="6" E="7" Order="2"/>
  <b id="15" B="2" E="7"/>
 </fragment>
</page></CDXML>
"""


def test_radical_atom_uses_chemdraws_own_hydrogen_count_not_default_valence():
    # Regression: _compute_formula used to let RDKit's sanitizer fill implicit
    # hydrogens from default valence, ignoring the CDXML's own
    # NumHydrogens attribute entirely -- correct for the vast majority of
    # atoms (which carry no such attribute and rely on default valence,
    # same as ChemDraw itself), but silently wrong for exactly the atoms
    # where ChemDraw's asserted count diverges from that default, which is
    # precisely the radical/open-shell case. Fixed by honoring
    # num_hydrogens (cdxml_graph.parse's new field) via
    # SetNoImplicit/SetNumExplicitHs whenever the CDXML asserts it,
    # instead of only ever trusting bond-implied valence.
    result = cdxml_document.parse_document(RADICAL_CDXML)
    assert len(result["structures"]) == 1
    s = result["structures"][0]
    assert s["atom_count"] == 7
    assert s["formula"] == "C7H7"  # NOT "C7H8" (toluene's formula)
    assert s["formula_note"] is None
