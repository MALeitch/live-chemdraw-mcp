import pytest

from chemdraw_connector.com.types import (
    arrow_head_position_name, arrow_head_position_value,
    arrow_head_type_name, arrow_head_type_value,
    bracket_type_name, bracket_type_value,
    bracket_usage_name, bracket_usage_value,
    colorref_value_to_rgb_hex, rgb_hex_to_colorref,
    element_symbol,
    enhanced_stereo_type_name, enhanced_stereo_type_value,
    no_go_type_name, no_go_type_value,
    polymer_repeat_pattern_name, polymer_repeat_pattern_value,
    symbol_type_name, symbol_type_value,
)


def test_element_symbol_known():
    assert element_symbol(6) == "C"
    assert element_symbol(1) == "H"


def test_element_symbol_negative_is_unknown_not_wrapped():
    # Python list indexing wraps negative indices (ELEMENTS[-1] == "Og");
    # element_symbol must not silently return the wrapped-around element.
    assert element_symbol(-1) == "unknown(-1)"


def test_element_symbol_out_of_range_high_is_unknown():
    assert element_symbol(999) == "unknown(999)"


def test_element_symbol_non_numeric_is_unknown():
    assert element_symbol("not-a-number") == "unknown(not-a-number)"
    assert element_symbol(None) == "unknown(None)"


def test_arrow_head_type_round_trip():
    assert arrow_head_type_name(1) == "solid"
    assert arrow_head_type_value("solid") == 1
    assert arrow_head_type_name(2) == "hollow"
    assert arrow_head_type_name(999) == "unknown(999)"


def test_arrow_head_type_value_unknown_raises():
    with pytest.raises(ValueError):
        arrow_head_type_value("fishhook")


def test_arrow_head_position_round_trip():
    assert arrow_head_position_name(2) == "full"
    assert arrow_head_position_value("full") == 2
    assert arrow_head_position_name(3) == "half_left"
    assert arrow_head_position_name(4) == "half_right"
    assert arrow_head_position_value("half_right") == 4
    assert arrow_head_position_name(999) == "unknown(999)"


def test_arrow_head_position_value_unknown_raises():
    with pytest.raises(ValueError):
        arrow_head_position_value("half")


def test_no_go_type_round_trip():
    assert no_go_type_name(2) == "cross"
    assert no_go_type_value("cross") == 2
    assert no_go_type_name(999) == "unknown(999)"


def test_no_go_type_value_unknown_raises():
    with pytest.raises(ValueError):
        no_go_type_value("crossed-out")


def test_symbol_type_round_trip():
    assert symbol_type_name(0) == "lone_pair"
    assert symbol_type_value("lone_pair") == 0
    assert symbol_type_name(10) == "racemic"
    assert symbol_type_value("relative") == 12
    assert symbol_type_name(999) == "unknown(999)"


def test_symbol_type_value_unknown_raises():
    with pytest.raises(ValueError):
        symbol_type_value("smiley_face")


def test_enhanced_stereo_type_round_trip():
    assert enhanced_stereo_type_name(3) == "or"
    assert enhanced_stereo_type_value("or") == 3
    assert enhanced_stereo_type_name(4) == "and"
    assert enhanced_stereo_type_name(999) == "unknown(999)"


def test_enhanced_stereo_type_value_unknown_raises():
    with pytest.raises(ValueError):
        enhanced_stereo_type_value("maybe")


def test_bracket_type_round_trip():
    assert bracket_type_name(0) == "square"
    assert bracket_type_value("square") == 0
    assert bracket_type_name(1) == "curly"
    assert bracket_type_value("round") == 2
    assert bracket_type_name(999) == "unknown(999)"


def test_bracket_type_value_unknown_raises():
    with pytest.raises(ValueError):
        bracket_type_value("hexagonal")


def test_bracket_usage_round_trip():
    assert bracket_usage_name(3) == "sru"
    assert bracket_usage_value("sru") == 3
    assert bracket_usage_name(4) == "monomer"
    assert bracket_usage_name(10) == "crosslink"
    assert bracket_usage_value("anypolymer") == 18
    assert bracket_usage_name(999) == "unknown(999)"


def test_bracket_usage_value_unknown_raises():
    with pytest.raises(ValueError):
        bracket_usage_value("nonsense")


def test_polymer_repeat_pattern_round_trip():
    assert polymer_repeat_pattern_name(0) == "head_to_tail"
    assert polymer_repeat_pattern_value("head_to_tail") == 0
    assert polymer_repeat_pattern_name(2) == "either_unknown"
    assert polymer_repeat_pattern_name(999) == "unknown(999)"


def test_polymer_repeat_pattern_value_unknown_raises():
    with pytest.raises(ValueError):
        polymer_repeat_pattern_value("sideways")


def test_rgb_hex_to_colorref_byte_order():
    # Confirmed live against ChemDraw: Color is BGR (0x00BBGGRR), not RGB.
    assert rgb_hex_to_colorref("#FF0000") == 0x0000FF  # red -> low byte
    assert rgb_hex_to_colorref("#0000FF") == 0xFF0000  # blue -> high byte
    assert rgb_hex_to_colorref("#00FF00") == 0x00FF00  # green -> middle byte


def test_rgb_hex_to_colorref_accepts_no_leading_hash():
    assert rgb_hex_to_colorref("FF0000") == 0x0000FF


def test_rgb_hex_to_colorref_invalid_raises():
    with pytest.raises(ValueError):
        rgb_hex_to_colorref("#FF00")
    with pytest.raises(ValueError):
        rgb_hex_to_colorref("#GGGGGG")


def test_colorref_value_to_rgb_hex_round_trip():
    for hexs in ("#FF0000", "#00FF00", "#0000FF", "#FF8000", "#000000"):
        assert colorref_value_to_rgb_hex(rgb_hex_to_colorref(hexs)) == hexs


def test_colorref_value_to_rgb_hex_zero_is_black():
    assert colorref_value_to_rgb_hex(0) == "#000000"
