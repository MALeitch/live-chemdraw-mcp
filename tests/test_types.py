from chemdraw_connector.com.types import element_symbol


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
