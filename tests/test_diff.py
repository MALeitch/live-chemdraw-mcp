from chemdraw_connector.domain.diff import diff_snapshots


def entry(oid, formula="C6H6", left=0, top=0, atoms=6, bonds=6):
    return {
        "id": oid, "formula": formula,
        "bounds": {"left": left, "top": top, "right": left + 50, "bottom": top + 40},
        "atom_count": atoms, "bond_count": bonds,
    }


def test_added_removed():
    d = diff_snapshots([entry("a")], [entry("b")])
    assert [o["id"] for o in d["added"]] == ["b"]
    assert [o["id"] for o in d["removed"]] == ["a"]


def test_moved():
    d = diff_snapshots([entry("a")], [entry("a", left=100)])
    assert d["moved"] == [{"id": "a", "dx_points": 100.0, "dy_points": 0.0}]


def test_small_jitter_not_moved():
    d = diff_snapshots([entry("a")], [entry("a", left=1)])
    assert d["moved"] == []


def test_modified_formula():
    d = diff_snapshots([entry("a")], [entry("a", formula="C6H5N", atoms=7)])
    changes = d["modified"][0]["changes"]
    assert changes["formula"] == {"from": "C6H6", "to": "C6H5N"}
    assert changes["atom_count"] == {"from": 6, "to": 7}


def test_unchanged_count():
    d = diff_snapshots([entry("a"), entry("b", left=300)],
                       [entry("a"), entry("b", left=300)])
    assert d["unchanged_count"] == 2
