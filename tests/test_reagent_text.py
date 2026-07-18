from chemdraw_connector.domain.reagent_text import subscript_formula_numbers


def test_simple_formula():
    assert subscript_formula_numbers("K2CO3") == "K₂CO₃"


def test_formula_after_closing_paren():
    assert subscript_formula_numbers("Pd(PPh3)4") == "Pd(PPh₃)₄"


def test_full_reagents_line():
    assert (subscript_formula_numbers("Pd(PPh3)4, K2CO3, THF/H2O, 80 °C")
            == "Pd(PPh₃)₄, K₂CO₃, THF/H₂O, 80 °C")


def test_temperature_and_equivalents_untouched():
    text = "1) Et3N (2.5 equiv), DCM, 0 C to rt, 4 h; 2) NaHCO3 (aq) workup"
    result = subscript_formula_numbers(text)
    assert "Et₃N" in result
    assert "NaHCO₃" in result
    assert "2.5 equiv" in result  # untouched
    assert "4 h" in result  # untouched
    assert "1)" in result and "2)" in result  # step numbers untouched


def test_hydrate_number_untouched_but_formula_subscripted():
    assert subscript_formula_numbers("MgSO4·7H2O") == "MgSO₄·7H₂O"


def test_hyphenated_name_untouched():
    assert subscript_formula_numbers("18-crown-6, KF, MeCN") == "18-crown-6, KF, MeCN"


def test_leading_number_untouched():
    assert subscript_formula_numbers("4Å MS, DCM") == "4Å MS, DCM"


def test_negative_temperature_untouched():
    assert subscript_formula_numbers("nBuLi, -78 °C, THF") == "nBuLi, -78 °C, THF"


def test_no_digits_untouched():
    assert subscript_formula_numbers("THF, reflux") == "THF, reflux"
