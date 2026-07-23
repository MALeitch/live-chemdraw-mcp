from chemdraw_connector.bridge import _stoichiometry as bs


def test_format_value_drops_trailing_zero_for_whole_numbers():
    assert bs._format_value(5) == "5"
    assert bs._format_value(5.0) == "5"
    assert bs._format_value("10") == "10"


def test_format_value_keeps_decimals_for_fractional_numbers():
    assert bs._format_value(1.0719) == repr(1.0719)
    assert bs._format_value(2.5) == repr(2.5)


def test_write_base_name_strips_extension():
    assert bs._write_base_name("my-doc.cdxml") == "my-doc"


def test_write_base_name_strips_one_prior_stoich_suffix():
    name = "my-doc-stoich-20260722-072050-4015b8.cdxml"
    assert bs._write_base_name(name) == "my-doc"


def test_write_base_name_strips_chained_prior_stoich_suffixes():
    """Reproduces the real bug: editing the same document repeatedly feeds
    each edit's new_active_document (already suffixed) back in as the next
    edit's doc.name -- confirmed live to otherwise accumulate
    "-stoich-...-stoich-..." onto the filename forever."""
    name = ("my-doc-stoich-20260722-072050-4015b8"
            "-stoich-20260722-072127-73813f.cdxml")
    assert bs._write_base_name(name) == "my-doc"


def test_write_base_name_sanitizes_unsafe_characters():
    assert bs._write_base_name("Untitled ACS Document 1996-6") == \
        "Untitled_ACS_Document_1996-6"
