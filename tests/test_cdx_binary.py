"""Tests for the pure-Python binary .cdx parser (no COM, no live ChemDraw).

Fixtures are built byte-by-byte rather than checked in as binary blobs, so
each test states exactly which structural feature it exercises.

Tag numbers are written as literals here ON PURPOSE. They were confirmed live
against real ChemDraw files, and hardcoding them means a wrong constant in
`cdx_binary` fails these tests instead of silently building a byte stream that
matches its own mistake.

The three bugs these guard against were all SILENT and COUNT-PRESERVING: the
prototype produced exactly the right number of fragments while the chemistry
inside them was wrong. Any assertion that only counts fragments passes all
three, so every test below checks atoms/bonds/elements, never just length.
"""
import struct

import pytest

from chemdraw_connector.domain.cdx_binary import parse

# --- CDX tag values (confirmed live; see cdx_binary module docstring) --------
MAGIC = b"VjCD0100"
O_PAGE, O_FRAGMENT, O_NODE, O_BOND, O_TEXT = 0x8001, 0x8003, 0x8004, 0x8005, 0x8006
P_2DPOS, P_NODE_TYPE, P_ELEMENT = 0x0200, 0x0400, 0x0402
P_CHARGE, P_BOND_ORDER, P_BOND_BEGIN, P_BOND_END, P_TEXT = (
    0x0421, 0x0600, 0x0604, 0x0605, 0x0700)


# --- byte-stream builders ---------------------------------------------------
def _u16(v):
    return struct.pack("<H", v)


def _i16(v):
    return struct.pack("<h", v)


def _u32(v):
    return struct.pack("<I", v)


def _i32(v):
    return struct.pack("<i", v)


def _prop(tag, data):
    """A property: tag, uint16 length, payload."""
    return _u16(tag) + _u16(len(data)) + data


def _long_prop(tag, data):
    """A property using the 0xFFFF -> uint32 length escape."""
    return _u16(tag) + _u16(0xFFFF) + _u32(len(data)) + data


def _obj(tag, oid, body=b""):
    """An object: tag, uint32 id, body, terminator."""
    return _u16(tag) + _u32(oid) + body + _u16(0x0000)


def _doc(body):
    """Full stream: magic + 4 version bytes + 16 reserved, then the body."""
    return MAGIC + b"\x04\x03\x02\x01" + b"\x00" * 16 + body


def _atom(oid, element=6, charge=None, ntype=None, pos=None, text=None,
         label_pos=None):
    """label_pos: the nested label Text child's OWN 2DPosition, distinct
    from the atom's -- this is how a real labeled atom is actually wired in
    CDX (Node[2DPosition=atom center]{ Text[2DPosition=label anchor]{...} }).
    Only meaningful together with `text`; defaults to None (no position on
    the Text child at all, which is also a real and common shape)."""
    body = b""
    if element is not None:
        body += _prop(P_ELEMENT, _i16(element))
    if ntype is not None:
        body += _prop(P_NODE_TYPE, _i16(ntype))
    if charge is not None:
        body += _prop(P_CHARGE, _i32(charge * 65536))
    if pos is not None:                      # CDX stores y THEN x
        x, y = pos
        body += _prop(P_2DPOS, _i32(int(y * 65536)) + _i32(int(x * 65536)))
    if text is not None:
        text_body = b""
        if label_pos is not None:
            lx, ly = label_pos
            text_body += _prop(P_2DPOS, _i32(int(ly * 65536)) + _i32(int(lx * 65536)))
        text_body += _prop(P_TEXT, _u16(0) + text.encode())
        body += _obj(O_TEXT, oid + 9000, text_body)
    return _obj(O_NODE, oid, body)


def _bond(oid, begin, end, order=0x0001):
    return _obj(O_BOND, oid, _prop(P_BOND_BEGIN, _u32(begin))
                + _prop(P_BOND_END, _u32(end))
                + _prop(P_BOND_ORDER, _u16(order)))


def _page(*fragments):
    return _doc(_obj(O_PAGE, 1, b"".join(fragments)))


# --- basics -----------------------------------------------------------------
def test_missing_magic_returns_nothing():
    assert parse(b"not a chemdraw file at all") == []


def test_simple_fragment_two_atoms_one_bond():
    buf = _page(_obj(O_FRAGMENT, 10, _atom(11, 6) + _atom(12, 8) + _bond(13, 11, 12)))
    frags = parse(buf)
    assert len(frags) == 1
    assert [n["element"] for n in frags[0]["nodes"]] == [6, 8]
    assert frags[0]["bonds"] == [
        {"begin": 11, "end": 12, "order": 1, "display": None}]


def test_element_defaults_to_carbon_when_absent():
    buf = _page(_obj(O_FRAGMENT, 10, _atom(11, element=None) + _atom(12, 7)
                     + _bond(13, 11, 12)))
    nodes = parse(buf)[0]["nodes"]
    assert [n["element"] for n in nodes] == [6, 7]


def test_charge_is_recovered():
    buf = _page(_obj(O_FRAGMENT, 10, _atom(11, 7, charge=1) + _atom(12, 8, charge=-1)
                     + _bond(13, 11, 12)))
    nodes = parse(buf)[0]["nodes"]
    assert [n["charge"] for n in nodes] == [1, -1]


def test_labeled_atom_keeps_its_own_position_not_its_label_text_anchor():
    """A labeled atom's visible symbol (any heteroatom, explicit H, nickname)
    is rendered via a nested Text child, and that Text child carries its OWN
    2DPosition -- the label's rendering anchor, offset from the atom's real
    coordinate by ChemDraw's text metrics, not a chemistry-meaningful value.

    Getting this wrong is silent in exactly the way the module docstring
    warns about: the parser still returns a plausible-looking (x, y) pair,
    just the WRONG one, for every labeled atom -- while unlabeled plain
    carbons (no nested Text) are unaffected. That skew is what made it look
    like stereocenters specifically had bad coordinates: a stereocenter's
    wedge/hash substituent (OH, NH2, an explicit H) is usually the one atom
    nearby that has a visible label."""
    atom_pos = (100.0, 200.0)
    label_pos = (103.89, 196.1)   # label's own anchor -- deliberately different
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11, element=8, pos=atom_pos, text="O", label_pos=label_pos)
                     + _atom(12, element=6, pos=(130.0, 200.0))
                     + _bond(13, 11, 12)))
    node = parse(buf)[0]["nodes"][0]
    assert node["pos"] == atom_pos, (
        "the label Text child's own 2DPosition must never overwrite the "
        "Node's own real position"
    )
    assert node["label"] == "O", "the label text itself must still be read"


@pytest.mark.parametrize("mask,expected", [
    (0x0001, 1), (0x0002, 2), (0x0004, 3),
    (0x0080, 15),          # one-and-a-half: aromatic, NOT "order 128"
    (0x0400, 1),           # dative: CDXML's Order can't express it, so 1
])
def test_bond_order_is_a_bitmask_not_an_ordinal(mask, expected):
    """0x04 is TRIPLE, not 'order 4' -- getting this wrong is silent."""
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11) + _atom(12) + _bond(13, 11, 12, order=mask)))
    assert parse(buf)[0]["bonds"][0]["order"] == expected


def test_multiple_fragments_stay_separate():
    buf = _page(
        _obj(O_FRAGMENT, 10, _atom(11) + _atom(12) + _bond(13, 11, 12)),
        _obj(O_FRAGMENT, 20, _atom(21) + _atom(22) + _atom(23)
             + _bond(24, 21, 22) + _bond(25, 22, 23)),
    )
    frags = parse(buf)
    assert [len(f["nodes"]) for f in frags] == [2, 3]
    assert [len(f["bonds"]) for f in frags] == [1, 2]


# --- bug 1: nickname expansion must not leak into the parent ----------------
def _phenyl_expansion(base):
    """Six carbons + six bonds, the shape ChemDraw nests inside a 'Ph' node."""
    body = b"".join(_atom(base + i) for i in range(6))
    body += b"".join(_bond(base + 100 + i, base + i, base + (i + 1) % 6)
                     for i in range(6))
    return _obj(O_FRAGMENT, base + 200, body)


def test_nickname_inner_fragment_does_not_leak_atoms_into_parent():
    """A 'Ph' node must contribute ONE pseudo-atom, not six phantom carbons.

    This is the bug that inflates every molecule using a shorthand group while
    leaving the fragment count perfectly correct.
    """
    nickname = _obj(O_NODE, 11,
                    _prop(P_NODE_TYPE, _i16(4))          # 4 == Nickname
                    + _obj(O_TEXT, 9011, _prop(P_TEXT, _u16(0) + b"Ph"))
                    + _phenyl_expansion(500))
    buf = _page(_obj(O_FRAGMENT, 10, nickname + _atom(12, 6) + _bond(13, 11, 12)))

    frags = parse(buf)
    assert len(frags) == 1, "the nested expansion must not become its own fragment"
    nodes = frags[0]["nodes"]
    assert len(nodes) == 2, f"expected nickname + carbon, got {len(nodes)} atoms"
    assert len(frags[0]["bonds"]) == 1, "the expansion's 6 bonds must not leak"


def test_nickname_node_itself_survives_its_inner_nodes():
    """Bug 2: the expansion's inner nodes must not clobber the outer record.

    Symptom when wrong: the nickname atom vanishes entirely rather than being
    duplicated -- the opposite failure from bug 1, from the same nesting.
    """
    nickname = _obj(O_NODE, 11,
                    _prop(P_NODE_TYPE, _i16(4))
                    + _obj(O_TEXT, 9011, _prop(P_TEXT, _u16(0) + b"TBS"))
                    + _phenyl_expansion(500))
    buf = _page(_obj(O_FRAGMENT, 10, nickname + _atom(12, 6) + _bond(13, 11, 12)))

    node = parse(buf)[0]["nodes"][0]
    assert node["id"] == 11
    assert node["node_type"] == "Nickname"
    assert node["is_real_atom"] is False
    assert node["label"] == "TBS", "nickname text is needed to tell Ph from Boc"


def test_atoms_after_a_nickname_are_not_dropped():
    """Bug 3: flush/depth ordering. When wrong this drops every atom."""
    nickname = _obj(O_NODE, 11,
                    _prop(P_NODE_TYPE, _i16(4))
                    + _obj(O_TEXT, 9011, _prop(P_TEXT, _u16(0) + b"Ph"))
                    + _phenyl_expansion(500))
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(12, 6) + nickname + _atom(14, 8) + _atom(15, 7)
                     + _bond(16, 12, 11) + _bond(17, 11, 14) + _bond(18, 14, 15)))

    frags = parse(buf)
    nodes = frags[0]["nodes"]
    assert [n["id"] for n in nodes] == [12, 11, 14, 15]
    assert [n["element"] for n in nodes] == [6, 6, 8, 7]
    assert len(frags[0]["bonds"]) == 3


def test_bonds_inside_a_nickname_expansion_are_not_adopted():
    """The parent must keep its own bonds only, with its own endpoints."""
    nickname = _obj(O_NODE, 11,
                    _prop(P_NODE_TYPE, _i16(4))
                    + _phenyl_expansion(500))
    buf = _page(_obj(O_FRAGMENT, 10, nickname + _atom(12) + _bond(13, 11, 12)))
    bonds = parse(buf)[0]["bonds"]
    assert bonds == [{"begin": 11, "end": 12, "order": 1, "display": None}]


# --- robustness: self-delimiting lengths make skipping safe -----------------
def test_unknown_property_is_skipped_by_its_own_length():
    """Partial format support is only safe if unknown props can be stepped over."""
    junk = _prop(0x0999, b"\xde\xad\xbe\xef" * 8)
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11) + junk + _atom(12, 8) + _bond(13, 11, 12)))
    frags = parse(buf)
    assert [n["element"] for n in frags[0]["nodes"]] == [6, 8]
    assert len(frags[0]["bonds"]) == 1


def test_long_property_uses_uint32_length_escape():
    """0xFFFF in the uint16 length means 'a uint32 length follows'."""
    big = _long_prop(0x0999, b"\x00" * 70000)
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11) + big + _atom(12, 16) + _bond(13, 11, 12)))
    frags = parse(buf)
    assert [n["element"] for n in frags[0]["nodes"]] == [6, 16]


def test_truncated_stream_does_not_raise():
    """Real embedded blobs get clipped; degrade quietly rather than explode."""
    full = _page(_obj(O_FRAGMENT, 10, _atom(11) + _atom(12) + _bond(13, 11, 12)))
    for cut in (30, 45, 60, len(full) - 3):
        parse(full[:cut])       # must not raise


def test_leading_junk_before_magic_is_tolerated():
    """An OLE container puts the CDX payload partway into the blob."""
    inner = _page(_obj(O_FRAGMENT, 10, _atom(11) + _atom(12, 7) + _bond(13, 11, 12)))
    frags = parse(b"\x01\x02OLEGARBAGE\xff" * 4 + inner)
    assert [n["element"] for n in frags[0]["nodes"]] == [6, 7]


# --- bond order must use cdxml_graph's INT convention -----------------------
#
# cdx_binary originally emitted order as a STRING ('1', '2', '1.5') while
# cdxml_graph emits an INT (1, 2, 15 for aromatic). cdx_document's
# _rdkit_bond_type keys on ints, so every string fell through .get()'s default
# and became a SINGLE bond -- silently. Benzene came out as C6H12 and acetone
# as C3H8O. Nothing raised; the formulas were simply wrong, which is the worst
# possible way for this to fail.
#
# The module docstring's promise is "the SAME plain-dict shape
# cdxml_graph.parse_element already produces", so int is the correct side to
# converge on.

@pytest.mark.parametrize("mask,expected_int", [
    (0x0001, 1),      # single
    (0x0002, 2),      # double
    (0x0004, 3),      # triple
    (0x0008, 4),      # quadruple
    (0x0040, 1),      # half -> single, matching _ORDER_MAP["0.5"]
    (0x0080, 15),     # aromatic (Kekule) -> 15, matching _ORDER_MAP["1.5"]
    (0x0100, 2),      # two-and-a-half -> 2, matching _ORDER_MAP["2.5"]
])
def test_bond_order_is_an_int_matching_cdxml_graph(mask, expected_int):
    from chemdraw_connector.domain.cdxml_graph import _ORDER_MAP
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11) + _atom(12) + _bond(13, 11, 12, order=mask)))
    order = parse(buf)[0]["bonds"][0]["order"]
    assert isinstance(order, int), (
        f"got {order!r}; cdxml_graph emits ints and cdx_document keys on "
        f"ints, so a string here silently degrades every bond to SINGLE"
    )
    assert order == expected_int
    assert order in set(_ORDER_MAP.values()), "must stay inside the shared vocabulary"


def test_unknown_bond_order_falls_back_to_single_as_an_int():
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11) + _atom(12) + _bond(13, 11, 12, order=0x8000)))
    assert parse(buf)[0]["bonds"][0]["order"] == 1


# --- stereochemistry: kCDXProp_Bond_Display ---------------------------------
#
# Wedge/hash is what makes a drawing communicate 3D. Without it every
# structure reads as flat, which is exactly what a downstream consumer
# reported. Display is a per-bond property; "Begin"/"End" say which ATOM the
# narrow point sits on, so direction is part of the stereo meaning and must
# survive parsing.

P_BOND_DISPLAY = 0x0601


def _bond_disp(oid, begin, end, display, order=0x0001):
    return _obj(O_BOND, oid, _prop(P_BOND_BEGIN, _u32(begin))
                + _prop(P_BOND_END, _u32(end))
                + _prop(P_BOND_ORDER, _u16(order))
                + _prop(P_BOND_DISPLAY, _i16(display)))


@pytest.mark.parametrize("code,expected", [
    (0, "Solid"),
    (1, "Dash"),
    (2, "Hash"),
    (3, "WedgedHashBegin"),
    (4, "WedgedHashEnd"),
    (5, "Bold"),
    (6, "WedgeBegin"),
    (7, "WedgeEnd"),
    (8, "Wavy"),
])
def test_bond_display_codes_map_to_cdxml_names(code, expected):
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11) + _atom(12) + _bond_disp(13, 11, 12, code)))
    assert parse(buf)[0]["bonds"][0]["display"] == expected


def test_plain_bonds_report_no_display():
    """A bond with no Display property is an ordinary line, not a wedge."""
    buf = _page(_obj(O_FRAGMENT, 10, _atom(11) + _atom(12) + _bond(13, 11, 12)))
    assert parse(buf)[0]["bonds"][0]["display"] is None


def test_wedge_direction_is_preserved():
    """WedgeBegin vs WedgeEnd invert the stereocentre -- they are not
    interchangeable, so the begin/end atoms must not be normalised away."""
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11) + _atom(12) + _atom(13)
                     + _bond_disp(14, 11, 12, 6)      # WedgeBegin, narrow at 11
                     + _bond_disp(15, 13, 12, 7)))    # WedgeEnd, narrow at 12
    bonds = parse(buf)[0]["bonds"]
    assert (bonds[0]["begin"], bonds[0]["display"]) == (11, "WedgeBegin")
    assert (bonds[1]["begin"], bonds[1]["display"]) == (13, "WedgeEnd")


def test_unknown_display_code_is_reported_not_dropped():
    """Better an unrecognised label than a silent 'this bond is flat'."""
    buf = _page(_obj(O_FRAGMENT, 10,
                     _atom(11) + _atom(12) + _bond_disp(13, 11, 12, 99)))
    display = parse(buf)[0]["bonds"][0]["display"]
    assert display is not None and display != "Solid"


def test_stereo_survives_alongside_a_nickname():
    """The nesting guards must not swallow the parent's wedge bonds."""
    nickname = _obj(O_NODE, 11,
                    _prop(P_NODE_TYPE, _i16(4))
                    + _obj(O_TEXT, 9011, _prop(P_TEXT, _u16(0) + b"Ph"))
                    + _phenyl_expansion(500))
    buf = _page(_obj(O_FRAGMENT, 10,
                     nickname + _atom(12) + _bond_disp(13, 12, 11, 6)))
    bonds = parse(buf)[0]["bonds"]
    assert len(bonds) == 1
    assert bonds[0]["display"] == "WedgeBegin"
