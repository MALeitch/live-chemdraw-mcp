import xml.etree.ElementTree as ET

from chemdraw_connector.domain import cdxml_graph

# Trimmed from a live ChemDraw 26 export: ethyl + contracted "Ph" nickname.
# The nickname node (id=20) carries an inner fragment that must be skipped.
CDXML_WITH_NICKNAME = """<?xml version="1.0" ?>
<CDXML>
 <page id="22">
  <fragment id="1">
   <n id="2" p="243.99 236.35"/>
   <n id="3" p="253.75 246.94"/>
   <n id="20" p="267.80 243.77" NodeType="Fragment">
    <fragment id="23">
     <n id="4" p="267.80 243.77"/>
     <n id="5" p="272.08 230.02"/>
     <n id="18" p="253.75 246.93" NodeType="ExternalConnectionPoint"/>
     <b id="12" B="4" E="5" Order="2"/>
     <b id="19" B="18" E="4"/>
    </fragment>
    <t p="264.47 247.67"><s>Ph</s></t>
   </n>
   <b id="10" B="2" E="3"/>
   <b id="11" B="3" E="20"/>
  </fragment>
 </page>
</CDXML>
"""


def test_nickname_inner_fragment_is_skipped():
    graph = cdxml_graph.parse(CDXML_WITH_NICKNAME)
    ids = sorted(n["id"] for n in graph["nodes"])
    assert ids == [2, 3, 20]
    assert len(graph["bonds"]) == 2


def test_nickname_node_is_dummy():
    graph = cdxml_graph.parse(CDXML_WITH_NICKNAME)
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id[2]["is_real_atom"] and by_id[2]["element"] == 6
    assert not by_id[20]["is_real_atom"]
    assert by_id[20]["node_type"] == "Fragment"


def test_bond_orders_and_defaults():
    graph = cdxml_graph.parse("""
    <CDXML><page><fragment>
      <n id="1"/><n id="2" Element="8"/><n id="3" Element="7" Charge="1"/>
      <b B="1" E="2" Order="2"/><b B="2" E="3"/>
    </fragment></page></CDXML>""")
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id[2]["element"] == 8
    assert by_id[3]["charge"] == 1
    orders = {(b["begin"], b["end"]): b["order"] for b in graph["bonds"]}
    assert orders[(1, 2)] == 2
    assert orders[(2, 3)] == 1


def test_bond_to_missing_node_dropped_not_crash():
    graph = cdxml_graph.parse("""
    <CDXML><page><fragment>
      <n id="1"/><n id="2"/>
      <b B="1" E="2"/><b B="1" E="99"/>
    </fragment></page></CDXML>""")
    assert len(graph["bonds"]) == 1


def test_num_hydrogens_present_vs_absent():
    # NumHydrogens is ChemDraw's own explicit hydrogen count when present
    # (see cdxml_document._compute_formula, which trusts it over RDKit's
    # default-valence fill -- load-bearing for radicals, see
    # test_cdxml_document.py's RADICAL_CDXML). Absent must stay None, not
    # coerce to 0 -- a caller filling implicit valence itself needs to
    # know "unasserted" from "asserted zero".
    graph = cdxml_graph.parse("""
    <CDXML><page><fragment>
      <n id="1" NumHydrogens="2"/><n id="2"/>
      <b B="1" E="2"/>
    </fragment></page></CDXML>""")
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id[1]["num_hydrogens"] == 2
    assert by_id[2]["num_hydrogens"] is None


def test_parse_element_matches_parse_on_same_content():
    # ROADMAP #7 (domain/cdxml_document.py) calls parse_element directly
    # on an already-parsed <fragment>/<group> Element (one of several
    # structures on a page it parsed itself), not on raw text -- confirms
    # the refactor is behavior-preserving.
    root = ET.fromstring(CDXML_WITH_NICKNAME)
    fragment = root.find(".//fragment")
    from_element = cdxml_graph.parse_element(fragment)
    from_text = cdxml_graph.parse(CDXML_WITH_NICKNAME)
    assert from_element == from_text
