import pytest

from chemdraw_connector.bridge._plumbing import _Plumbing
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


def test_build_text_cdxml_declares_utf8_encoding():
    # Regression coverage for the write-side data-loss bug (see this
    # function's docstring and bridge/_plumbing.py's _insert_raw): the
    # prolog previously declared no encoding at all.
    xml = build_text_cdxml(split_subscript_runs("THF"), x=0, y=0,
                           width=50, height=10)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8" ?>')


def test_build_text_cdxml_preserves_cp1252_incompatible_arrow():
    # CONFIRMED LIVE regression: U+2192 (rightwards arrow) has no cp1252
    # mapping and came back from a real build -> insert -> describe_canvas
    # round trip as a literal "?" actually stored in the document before
    # this fix. U+00B0 (degree sign) DOES have a cp1252 mapping and
    # survived even before the fix -- included here as the contrasting
    # character, not as what's under test.
    text = "1) THF, 0 °C→rt"
    runs = split_subscript_runs(text)
    xml = build_text_cdxml(runs, x=0, y=0, width=500, height=14)
    assert "→" in xml
    # Exact-substring match is the strongest possible proof the arrow
    # survives intact (not just "some non-'?' character is present"):
    # the whole run, arrow included, must appear byte-for-byte.
    assert f'<s font="3" size="10.0">{text}</s>' in xml


@pytest.mark.parametrize("char", ["≥", "λ"])  # greater-or-equal, lambda
def test_build_text_cdxml_preserves_other_cp1252_incompatible_characters(char):
    # Confirms the fix isn't specific to the arrow: any character outside
    # cp1252's repertoire is the class of character this bug affects (see
    # _insert_raw's docstring on why cp1252, specifically, is the relevant
    # boundary).
    with pytest.raises(UnicodeEncodeError):
        char.encode("cp1252")  # sanity check: genuinely outside cp1252
    text = f"step A{char}step B"
    xml = build_text_cdxml([(text, False)], x=0, y=0, width=50, height=10)
    assert char in xml


class _FakeOleObj:
    """Stand-in for the raw win32com _oleobj_ handle _insert_raw calls
    Invoke on directly. Records exactly what type and bytes reach the
    Data property-put -- what this bug lives or dies on -- without a live
    ChemDraw session: the bug is that pywin32 marshals a plain Python str
    as a BSTR, which ChemDraw's own Data-property setter then narrows to
    the system ANSI codepage before its CDXML parser ever runs, so what
    matters is proving the payload that actually reaches Invoke is bytes,
    not a str."""

    def __init__(self):
        self.calls = []

    def GetIDsOfNames(self, flags, name):
        assert name == "Data"
        return 1

    def Invoke(self, dispid, lcid, wflags, resultwanted, mime, payload):
        self.calls.append((mime, payload))


class _FakeObjs:
    def __init__(self):
        self._oleobj_ = _FakeOleObj()


def test_insert_raw_sends_utf8_bytes_not_a_python_str():
    text = "1) THF, 0 °C→rt"
    runs = split_subscript_runs(text)
    cdxml = build_text_cdxml(runs, x=0, y=0, width=500, height=14)

    objs = _FakeObjs()
    _Plumbing._insert_raw(objs, "text/xml", cdxml)

    assert len(objs._oleobj_.calls) == 1
    mime, payload = objs._oleobj_.calls[0]
    assert mime == "text/xml"
    assert isinstance(payload, bytes)
    assert not isinstance(payload, str)
    # Round-trips exactly, arrow included -- proves the fix delivers the
    # real UTF-8 bytes of the full CDXML document to the COM call, not a
    # truncated or re-mangled copy of it.
    assert payload.decode("utf-8") == cdxml
    assert "→".encode("utf-8") in payload


def test_insert_raw_passes_bytes_through_unchanged():
    # Forward-compatibility guard (see _insert_raw's docstring): if a
    # future caller ever hands over an already-encoded bytes payload, it
    # must not be re-encoded or otherwise altered.
    objs = _FakeObjs()
    raw_bytes = "K2CO3".encode("utf-8")
    _Plumbing._insert_raw(objs, "text/xml", raw_bytes)
    mime, payload = objs._oleobj_.calls[0]
    assert payload is raw_bytes
