from chemdraw_connector.domain.label_filter import parse_label_filter


def test_none_and_empty_match_everything():
    assert parse_label_filter(None) is None
    assert parse_label_filter("") is None
    assert parse_label_filter("all") is None
    assert parse_label_filter("ALL") is None
    assert parse_label_filter("*") is None


def test_single_label():
    assert parse_label_filter("Ph") == {"ph"}


def test_comma_separated_stripped_and_lowered():
    assert parse_label_filter("Ph, Boc ,TES") == {"ph", "boc", "tes"}


def test_list_input():
    assert parse_label_filter(["Ph", "Boc"]) == {"ph", "boc"}


def test_blank_entries_ignored():
    assert parse_label_filter("Ph,,  ,Boc") == {"ph", "boc"}


def test_empty_list_matches_everything():
    assert parse_label_filter([]) is None
