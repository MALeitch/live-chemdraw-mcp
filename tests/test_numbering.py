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


def test_numeric_letter_requires_group_sizes():
    # Previously silently fell back to [1]*count -- identical to plain
    # 'numeric' output -- instead of ever actually grouping anything.
    with pytest.raises(ValueError, match="requires group_sizes"):
        make_labels(4, scheme="numeric-letter")


def test_unknown_scheme():
    with pytest.raises(ValueError, match="Unknown numbering scheme"):
        make_labels(3, scheme="roman")
