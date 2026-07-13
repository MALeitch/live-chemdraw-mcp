from chemdraw_connector.domain.dedup import find_duplicate_groups


def test_exact_duplicates():
    items = [
        ("a", "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"),
        ("b", "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"),
        ("c", "RYYVLZVUVIJVGH-UHFFFAOYSA-N"),
    ]
    result = find_duplicate_groups(items)
    assert result["exact"] == [["a", "b"]]
    assert result["skeleton"] == []


def test_skeleton_match_different_stereo():
    items = [
        ("a", "AAAAAAAAAAAAAA-UHFFFAOYSA-N"),
        ("b", "AAAAAAAAAAAAAA-ZZZZZZZZSA-N"),
    ]
    result = find_duplicate_groups(items)
    assert result["exact"] == []
    assert result["skeleton"] == [["a", "b"]]


def test_empty_keys_ignored():
    result = find_duplicate_groups([("a", ""), ("b", None)])
    assert result == {"exact": [], "skeleton": []}
