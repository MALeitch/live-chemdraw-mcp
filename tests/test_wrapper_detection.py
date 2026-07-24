from chemdraw_connector.domain import wrapper_detection as wd


def test_single_child_containment_detected():
    """The caption-wrapper shape: one outer box strictly containing one
    inner box."""
    outer = (0.0, 0.0, 100.0, 40.0)
    inner = (10.0, 5.0, 60.0, 20.0)
    result = wd.find_containing_wrappers([outer, inner])
    assert result == {0: [1]}


def test_multi_child_containment_detected():
    """The ionic-salt-wrapper shape: one outer box strictly containing TWO
    disjoint inner boxes."""
    outer = (0.0, 0.0, 100.0, 40.0)
    child_a = (5.0, 5.0, 30.0, 20.0)
    child_b = (60.0, 5.0, 90.0, 20.0)
    result = wd.find_containing_wrappers([outer, child_a, child_b])
    assert result == {0: [1, 2]}


def test_box_containing_nothing_is_absent_from_result():
    a = (0.0, 0.0, 10.0, 10.0)
    b = (20.0, 20.0, 30.0, 30.0)
    result = wd.find_containing_wrappers([a, b])
    assert result == {}


def test_two_disconnected_fragments_never_contain_each_other():
    """Two genuinely independent fragments (no shared wrapper in this
    batch) must never be misclassified as containing one another."""
    a = (0.0, 0.0, 20.0, 20.0)
    b = (30.0, 0.0, 50.0, 20.0)
    assert wd.find_containing_wrappers([a, b]) == {}


def test_equal_bounds_are_not_containment():
    """Exact duplicates (same bounds both ways) are real duplicates, not a
    wrapper relationship -- strictly-larger-area is required."""
    a = (0.0, 0.0, 20.0, 20.0)
    b = (0.0, 0.0, 20.0, 20.0)
    assert wd.find_containing_wrappers([a, b]) == {}


def test_tolerance_allows_near_equal_edges():
    outer = (0.0, 0.0, 100.0, 40.0)
    inner = (0.3, 0.2, 99.8, 39.7)  # within default tol=0.5 on every edge
    result = wd.find_containing_wrappers([outer, inner])
    assert result == {0: [1]}
