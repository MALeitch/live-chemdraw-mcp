from chemdraw_connector.domain.reagent_text import (
    build_text_cdxml, split_subscript_runs,
)


def test_simple_formula():
    assert split_subscript_runs("K2CO3") == [("K", False), ("2", True),
                                              ("CO", False), ("3", True)]


def test_formula_after_closing_paren():
    assert split_subscript_runs("Pd(PPh3)4") == [
        ("Pd(PPh", False), ("3", True), (")", False), ("4", True),
    ]


def test_temperature_and_equivalents_untouched():
    text = "1) Et3N (2.5 equiv), DCM, 0 C to rt, 4 h; 2) NaHCO3 (aq) workup"
    runs = split_subscript_runs(text)
    subscripted = [seg for seg, is_sub in runs if is_sub]
    assert subscripted == ["3", "3"]  # only the Et3N and NaHCO3 digits
    plain = "".join(seg for seg, is_sub in runs if not is_sub)
    assert "2.5 equiv" in plain
    assert "4 h" in plain
    assert "1)" in plain and "2)" in plain


def test_hydrate_number_untouched_but_formula_subscripted():
    runs = split_subscript_runs("MgSO4·7H2O")
    subscripted = [seg for seg, is_sub in runs if is_sub]
    assert subscripted == ["4", "2"]  # not the "7" hydrate count


def test_hyphenated_name_untouched():
    runs = split_subscript_runs("18-crown-6, KF, MeCN")
    assert all(not is_sub for _, is_sub in runs)


def test_leading_number_untouched():
    runs = split_subscript_runs("4Å MS, DCM")
    assert all(not is_sub for _, is_sub in runs)


def test_negative_temperature_untouched():
    runs = split_subscript_runs("nBuLi, -78 °C, THF")
    assert all(not is_sub for _, is_sub in runs)


def test_no_digits_single_plain_run():
    assert split_subscript_runs("THF, reflux") == [("THF, reflux", False)]


def test_reassembled_text_matches_original():
    for text in ["K2CO3", "Pd(PPh3)4, K2CO3, THF/H2O, 80 °C", "no digits here"]:
        runs = split_subscript_runs(text)
        assert "".join(seg for seg, _ in runs) == text


def test_build_text_cdxml_has_subscript_face_only_on_subscript_runs():
    xml = build_text_cdxml(split_subscript_runs("K2CO3"), x=0, y=0,
                           width=50, height=10)
    assert '<s font="3" size="10.0" face="32">2</s>' in xml
    assert '<s font="3" size="10.0" face="32">3</s>' in xml
    assert '<s font="3" size="10.0">K</s>' in xml
    assert '<s font="3" size="10.0">CO</s>' in xml


def test_build_text_cdxml_escapes_special_characters():
    xml = build_text_cdxml([("A & B < C", False)], x=0, y=0, width=50, height=10)
    assert "A &amp; B &lt; C" in xml
    assert "A & B < C" not in xml
