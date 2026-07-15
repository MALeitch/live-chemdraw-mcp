from chemdraw_connector.domain import cdxml_snapshot


def test_parse_bounds_normalizes_reversed_order():
    # Verified live: a rotated/shadowed graphic serialized right,bottom,
    # left,top instead of left,top,right,bottom.
    bounds = cdxml_snapshot.parse_bounds("751.16 712.10 550.36 221.70")
    assert bounds == {"left": 550.36, "top": 221.70, "right": 751.16, "bottom": 712.10}


def test_parse_bounds_normal_order():
    bounds = cdxml_snapshot.parse_bounds("47.02 220.20 755.70 909.12")
    assert bounds == {"left": 47.02, "top": 220.20, "right": 755.70, "bottom": 909.12}


NORMAL_STRUCTURE = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="10" BoundingBox="100.0 100.0 200.0 150.0">
   <fragment id="11" BoundingBox="100.0 100.0 200.0 150.0">
    <n id="12" p="100 100"/>
    <n id="13" p="150 100"/>
    <n id="14" p="200 150"/>
    <b id="20" B="12" E="13" Order="1"/>
    <b id="21" B="13" E="14" Order="2"/>
   </fragment>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-normal"/>
  </group>
 </page>
</CDXML>
"""


def test_normal_structure_counts_and_bounds():
    index = cdxml_snapshot.index_units(NORMAL_STRUCTURE)
    assert set(index) == {"claude-normal"}
    entry = index["claude-normal"]
    assert entry["atom_count"] == 3
    assert entry["bond_count"] == 2
    assert entry["bounds"] == {"left": 100.0, "top": 100.0, "right": 200.0, "bottom": 150.0}


# Mirrors the live NTf2 counterion: one nickname atom (NodeType="Fragment")
# whose inner fragment must NOT be descended into for counting purposes.
CONTRACTED_NICKNAME = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="30" BoundingBox="0 0 30 15">
   <fragment id="31" BoundingBox="0 0 30 15">
    <n id="32" p="0 0" NodeType="Fragment">
     <fragment id="33">
      <n id="34" p="0 0" Element="6"/>
      <n id="35" p="5 5" Element="16"/>
      <n id="36" p="10 0" Element="9"/>
      <b id="40" B="34" E="35" Order="1"/>
      <b id="41" B="35" E="36" Order="1"/>
     </fragment>
     <t p="0 0"><s>NTf2</s></t>
    </n>
   </fragment>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-nickname"/>
  </group>
 </page>
</CDXML>
"""


def test_contracted_nickname_counts_as_one_atom():
    index = cdxml_snapshot.index_units(CONTRACTED_NICKNAME)
    entry = index["claude-nickname"]
    assert entry["atom_count"] == 1
    assert entry["bond_count"] == 0


# Mirrors a live decoration group: a panel-box <graphic> wrapped in a group
# with a claude_id tag, no atoms at all.
ZERO_ATOM_DECORATION = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="50" BoundingBox="0 0 500 700">
   <graphic id="51" BoundingBox="0 0 500 700" GraphicType="Rectangle"/>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-decoration"/>
  </group>
 </page>
</CDXML>
"""


def test_zero_atom_decoration_group():
    index = cdxml_snapshot.index_units(ZERO_ATOM_DECORATION)
    entry = index["claude-decoration"]
    assert entry["atom_count"] == 0
    assert entry["bond_count"] == 0
    assert entry["bounds"] == {"left": 0.0, "top": 0.0, "right": 500.0, "bottom": 700.0}


# Mirrors the live "caption wrapper duplicate" pattern: an outer group
# (structure + caption) and an inner tight structure group are BOTH tagged,
# independently addressable, and report the SAME atom/bond counts (the
# wrapper contains the same atoms, just also the caption) — canvas.py tells
# them apart by bounds containment, not atom_count.
WRAPPER_AND_INNER = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="60" BoundingBox="0 0 100 120">
   <group id="61" BoundingBox="0 0 100 100">
    <fragment id="62" BoundingBox="0 0 100 100">
     <n id="63" p="0 0"/>
     <n id="64" p="50 50"/>
     <b id="70" B="63" E="64" Order="1"/>
    </fragment>
    <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-inner"/>
   </group>
   <t p="0 110"><s>1 (90%)</s></t>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-wrapper"/>
  </group>
 </page>
</CDXML>
"""


def test_wrapper_and_inner_structure_both_indexed():
    index = cdxml_snapshot.index_units(WRAPPER_AND_INNER)
    assert set(index) == {"claude-inner", "claude-wrapper"}
    assert index["claude-inner"]["atom_count"] == 2
    assert index["claude-wrapper"]["atom_count"] == 2
    assert index["claude-inner"]["bounds"]["bottom"] == 100.0
    assert index["claude-wrapper"]["bounds"]["bottom"] == 120.0


MISSING_BOUNDING_BOX = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="80">
   <fragment id="81">
    <n id="82" p="0 0"/>
   </fragment>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-nobounds"/>
  </group>
 </page>
</CDXML>
"""


def test_missing_bounding_box_is_none():
    index = cdxml_snapshot.index_units(MISSING_BOUNDING_BOX)
    assert index["claude-nobounds"]["bounds"] is None
    assert index["claude-nobounds"]["atom_count"] == 1
