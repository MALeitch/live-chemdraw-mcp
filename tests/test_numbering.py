import pytest

from chemdraw_connector.domain.numbering import make_labels


def test_numeric():
    assert make_labels(3) == ["1", "2", "3"]
    assert make_labels(2, start=5) == ["5", "6"]


def test_numeric_letter():
    assert make_labels(6, scheme="numeric-letter", group_sizes=[1, 3, 2]) == [
        "1", "2a", "2b", "2c", "3a", "3b",
    ]


def test_numeric_letter_size_mismatch():
    with pytest.raises(ValueError, match="sums to"):
        make_labels(5, scheme="numeric-letter", group_sizes=[1, 3])


def test_unknown_scheme():
    with pytest.raises(ValueError, match="Unknown numbering scheme"):
        make_labels(3, scheme="roman")
