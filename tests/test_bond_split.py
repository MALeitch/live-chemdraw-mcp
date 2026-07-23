import pytest

from chemdraw_connector.domain.bond_split import RingBondSplitError, split_atoms


def test_straightforward_acyclic_branch_split():
    # chain 1-2-3-4-5, split at (3,4) affecting the side with atom 5
    atom_ids = [1, 2, 3, 4, 5]
    bonds = [("b12", 1, 2), ("b23", 2, 3), ("b34", 3, 4), ("b45", 4, 5)]
    result = split_atoms(atom_ids, bonds, 3, 4, side_atom_id=5)
    assert result["atom_ids"] == frozenset({4, 5})
    assert set(result["bond_ids"]) == {"b45"}


def test_split_from_far_tip_still_reaches_whole_side():
    # chain 1-2-3-4-5 with atom 4 also bonded to atom 6 (a branchier side)
    atom_ids = [1, 2, 3, 4, 5, 6]
    bonds = [("b12", 1, 2), ("b23", 2, 3), ("b34", 3, 4),
             ("b45", 4, 5), ("b46", 4, 6)]
    # naming the far tip (5) should reach the whole side, including 4 and 6
    result = split_atoms(atom_ids, bonds, 3, 4, side_atom_id=5)
    assert result["atom_ids"] == frozenset({4, 5, 6})
    assert set(result["bond_ids"]) == {"b45", "b46"}


def test_ring_bond_raises():
    # 4-membered ring: 1-2-3-4-1
    atom_ids = [1, 2, 3, 4]
    bonds = [("b12", 1, 2), ("b23", 2, 3), ("b34", 3, 4), ("b41", 4, 1)]
    with pytest.raises(RingBondSplitError):
        split_atoms(atom_ids, bonds, 1, 2, side_atom_id=1)


def test_unknown_side_atom_raises_value_error():
    atom_ids = [1, 2, 3]
    bonds = [("b12", 1, 2), ("b23", 2, 3)]
    with pytest.raises(ValueError):
        split_atoms(atom_ids, bonds, 1, 2, side_atom_id=999)


def test_bond_endpoints_not_actually_bonded_raises_value_error():
    atom_ids = [1, 2, 3]
    bonds = [("b12", 1, 2), ("b23", 2, 3)]
    with pytest.raises(ValueError):
        split_atoms(atom_ids, bonds, 1, 3, side_atom_id=1)  # 1-3 not a bond


def test_side_atom_disconnected_from_split_bond_raises_value_error():
    # Two disconnected fragments in one multi-fragment unit (e.g. a salt's
    # ion pair): 1-2-3 contains the bond being split; 10-11 is a separate,
    # unrelated fragment. side_atom_id=10 must not silently be accepted as
    # "the side" of a bond it has no path to at all.
    atom_ids = [1, 2, 3, 10, 11]
    bonds = [("b12", 1, 2), ("b23", 2, 3), ("b1011", 10, 11)]
    with pytest.raises(ValueError):
        split_atoms(atom_ids, bonds, 1, 2, side_atom_id=10)


class _UnhashableToken:
    """Stands in for a live COM Bond object, which pywin32 does not make
    hashable — bond_ids must not require hashing its tokens."""
    __hash__ = None

    def __init__(self, label):
        self.label = label


def test_bond_ids_works_with_unhashable_tokens():
    # chain 1-2-3-4-5, split at (3,4) affecting the side with atom 5 —
    # same shape as test_straightforward_acyclic_branch_split, but with
    # a token type that mirrors the real bridge.py call (live COM Bond
    # objects passed straight through as opaque tokens).
    tok45 = _UnhashableToken("b45")
    atom_ids = [1, 2, 3, 4, 5]
    bonds = [("b12", 1, 2), ("b23", 2, 3), ("b34", 3, 4), (tok45, 4, 5)]
    result = split_atoms(atom_ids, bonds, 3, 4, side_atom_id=5)
    assert result["bond_ids"] == [tok45]


def test_bonds_fully_inside_side_are_returned_bond_ids():
    # branch off atom 3 contains its own internal ring (4-5-6-4)
    atom_ids = [1, 2, 3, 4, 5, 6]
    bonds = [
        ("b12", 1, 2), ("b23", 2, 3),
        ("b34", 3, 4), ("b45", 4, 5), ("b56", 5, 6), ("b64", 6, 4),
    ]
    result = split_atoms(atom_ids, bonds, 2, 3, side_atom_id=4)
    assert result["atom_ids"] == frozenset({3, 4, 5, 6})
    assert set(result["bond_ids"]) == {"b34", "b45", "b56", "b64"}
