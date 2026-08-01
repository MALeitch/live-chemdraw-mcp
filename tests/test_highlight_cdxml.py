import xml.etree.ElementTree as ET

from chemdraw_connector.domain import highlight_cdxml as hc

# A minimal but complete real-shaped fragment export: benzene, no
# colortable of its own (matches confirmed per-unit export shape).
BARE_FRAGMENT = """<fragment id="100">
<n id="1" p="0 0" Element="6"/>
<n id="2" p="14 8" Element="6"/>
<n id="3" p="14 24" Element="6"/>
<b id="7" B="1" E="2" Order="2"/>
<b id="8" B="2" E="3"/>
</fragment>"""

# A full document already carrying a colortable (matches whole-document/
# selection export shape) with two custom colors appended past the 8
# standard ones (indices 2-9), so list position 8 -> index 10.
FULL_DOC_WITH_COLORS = """<?xml version="1.0" encoding="UTF-8" ?>
<CDXML>
<colortable>
<color r="1" g="1" b="1"/>
<color r="0" g="0" b="0"/>
<color r="1" g="0" b="0"/>
<color r="1" g="1" b="0"/>
<color r="0" g="1" b="0"/>
<color r="0" g="1" b="1"/>
<color r="0" g="0" b="1"/>
<color r="1" g="0" b="1"/>
<color r="0.5098" g="0.9020" b="1"/>
</colortable>
<page>
<fragment id="100">
<n id="1" p="0 0" Element="6"/>
<n id="2" p="14 8" Element="6"/>
<b id="7" B="1" E="2"/>
</fragment>
</page>
</CDXML>"""


def test_ensure_full_document_wraps_bare_fragment():
    wrapped = hc.ensure_full_document(BARE_FRAGMENT)
    assert wrapped.strip().startswith("<?xml")
    assert "<CDXML>" in wrapped
    assert "<fragment" in wrapped
    ET.fromstring(wrapped)  # must parse cleanly


def test_ensure_full_document_leaves_full_document_unchanged():
    wrapped = hc.ensure_full_document(FULL_DOC_WITH_COLORS)
    assert wrapped == FULL_DOC_WITH_COLORS


def test_parse_rgb_hex():
    assert hc.parse_rgb_hex("#FF0000") == (1.0, 0.0, 0.0)
    assert hc.parse_rgb_hex("00AA00") == (0.0, 170 / 255, 0.0)


def test_resolve_color_index_creates_colortable_when_missing():
    root = hc.parse(BARE_FRAGMENT)
    assert root.find("colortable") is None
    # Pure red matches the 3rd standard color (list position 2) -> index 4.
    idx = hc.resolve_color_index(root, "#FF0000")
    assert idx == 4
    assert root.find("colortable") is not None
    assert len(root.find("colortable").findall("color")) == 8


def test_resolve_color_index_reuses_existing_close_match():
    root = hc.parse(FULL_DOC_WITH_COLORS)
    # The 9th <color> (list position 8) is r=0.5098 g=0.9020 b=1 -> index 10.
    idx = hc.resolve_color_index(root, "#82E6FF")
    assert idx == 10
    assert len(root.find(".//colortable").findall("color")) == 9  # no duplicate appended


def test_resolve_color_index_appends_new_custom_color():
    root = hc.parse(FULL_DOC_WITH_COLORS)
    idx = hc.resolve_color_index(root, "#F0964A")  # not in the table
    assert idx == 11  # list position 9 (10th entry) -> +2 offset
    assert len(root.find(".//colortable").findall("color")) == 10


def test_set_highlight_all_atoms_and_bonds():
    root = hc.parse(BARE_FRAGMENT)
    atom_count, bond_count = hc.set_highlight(root, 11)
    assert atom_count == 3
    assert bond_count == 2
    for n in root.iter("n"):
        assert n.get("highlightColor") == "11"
    for b in root.iter("b"):
        assert b.get("highlightColor") == "11"


def test_set_highlight_specific_atoms_only():
    root = hc.parse(BARE_FRAGMENT)
    atom_count, bond_count = hc.set_highlight(root, 11, atom_ids=[1, 2])
    assert atom_count == 2
    assert bond_count == 2  # bond_pairs=None -> all bonds, independent of atom filter
    colors = {n.get("id"): n.get("highlightColor") for n in root.iter("n")}
    assert colors["1"] == "11"
    assert colors["2"] == "11"
    assert colors["3"] is None
    # No bond_pairs given but atom_ids WAS given -- bonds are untouched
    # only if bond_pairs is also explicitly scoped; here bond_pairs=None
    # defaults to "all bonds" independently of the atom filter.
    for b in root.iter("b"):
        assert b.get("highlightColor") == "11"


def test_set_highlight_specific_bonds_only_matches_either_order():
    root = hc.parse(BARE_FRAGMENT)
    atom_count, bond_count = hc.set_highlight(
        root, 11, atom_ids=[], bond_pairs=[(2, 1)])  # reversed order
    assert atom_count == 0
    assert bond_count == 1
    bonds = {(b.get("B"), b.get("E")): b.get("highlightColor") for b in root.iter("b")}
    assert bonds[("1", "2")] == "11"  # B=1,E=2 matched by pair (2,1)
    assert bonds[("2", "3")] is None
    for n in root.iter("n"):
        assert n.get("highlightColor") is None


def test_clear_highlight_removes_attribute():
    root = hc.parse(BARE_FRAGMENT)
    hc.set_highlight(root, 11)
    atom_count, bond_count = hc.clear_highlight(root)
    assert atom_count == 3
    assert bond_count == 2
    for n in root.iter("n"):
        assert "highlightColor" not in n.attrib
    for b in root.iter("b"):
        assert "highlightColor" not in b.attrib


def test_clear_highlight_scoped_to_specific_atoms():
    root = hc.parse(BARE_FRAGMENT)
    hc.set_highlight(root, 11)
    hc.clear_highlight(root, atom_ids=[1], bond_pairs=[])
    colors = {n.get("id"): n.get("highlightColor") for n in root.iter("n")}
    assert colors["1"] is None
    assert colors["2"] == "11"
    assert colors["3"] == "11"


def test_serialize_round_trips_through_parse():
    root = hc.parse(BARE_FRAGMENT)
    hc.set_highlight(root, 4)
    text = hc.serialize(root)
    assert text.startswith("<?xml")
    reparsed = hc.parse(text)
    highlighted = [n.get("id") for n in reparsed.iter("n") if n.get("highlightColor") == "4"]
    assert sorted(highlighted, key=int) == ["1", "2", "3"]
