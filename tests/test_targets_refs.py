"""Stable atom/bond ref addressing (targets.atom_ref/bond_ref/resolve_atom/
resolve_bond) — pure, no COM. Self-contained fakes, not shared with
test_targets_cache.py, since tests/ isn't a package (no relative imports)."""
import pytest

from chemdraw_connector import targets
from chemdraw_connector.errors import TargetNotFoundError


class FakeTag:
    def __init__(self):
        self.StringValue = None


class FakeObjects:
    def __init__(self, atom_count, bond_count):
        self.Atoms = type("C", (), {"Count": atom_count})()
        self.Bonds = type("C", (), {"Count": bond_count})()


class FakeUnit:
    def __init__(self, uid, atom_count, bond_count):
        self.ID = uid
        self.Objects = FakeObjects(atom_count, bond_count)
        self._tags = {}

    def GetObjectTag(self, name):
        return self._tags.get(name)

    def MakeObjectTag(self, name, _flag):
        tag = FakeTag()
        self._tags[name] = tag
        return tag


class FakeAtom:
    def __init__(self, id_, fragment, element_number=6, charge=0, x=0.0, y=0.0):
        self.ID = id_
        self.Fragment = fragment
        self.ElementNumber = element_number
        self.Charge = charge
        self.ChemicalWarning = ""
        self.Position = type("P", (), {"X": x, "Y": y})()


class FakeBond:
    def __init__(self, atom1, atom2, fragment, order=1):
        self.Atom1 = atom1
        self.Atom2 = atom2
        self.Fragment = fragment
        self.BondOrder = order
        self.ChemicalWarning = ""


class Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class FakeDoc:
    def __init__(self, groups, atoms, bonds, name="doc1"):
        self.Groups = Coll(groups)
        self.Atoms = Coll(atoms)
        self.Bonds = Coll(bonds)
        self.name = name


def make_ring():
    """A 3-atom, 2-bond fake structure, tagged like a real one would be
    before atom/bond editing starts."""
    grp = FakeUnit(1, atom_count=3, bond_count=2)
    targets.ensure_id(grp)
    a1, a2, a3 = FakeAtom(101, grp), FakeAtom(102, grp), FakeAtom(103, grp)
    b1 = FakeBond(a1, a2, grp, order=1)
    b2 = FakeBond(a2, a3, grp, order=2)
    doc = FakeDoc(groups=[grp], atoms=[a1, a2, a3], bonds=[b1, b2])
    return doc, grp, (a1, a2, a3), (b1, b2)


# ---------- ref formatting ----------

def test_atom_ref_uses_doc_unique_id():
    a = FakeAtom(101, fragment=None)
    assert targets.atom_ref(a) == "a101"


def test_bond_ref_is_order_independent():
    a1, a2 = FakeAtom(101, None), FakeAtom(102, None)
    b_forward = FakeBond(a1, a2, None)
    b_reverse = FakeBond(a2, a1, None)
    assert targets.bond_ref(b_forward) == targets.bond_ref(b_reverse) == "b101-102"


# ---------- resolve_atom / resolve_bond ----------

def test_resolve_atom_by_ref():
    doc, grp, (a1, a2, a3), _ = make_ring()
    atom, idx = targets.resolve_atom(doc, grp, "a102")
    assert atom is a2 and idx == 2


def test_resolve_atom_by_legacy_int_index():
    doc, grp, (a1, a2, a3), _ = make_ring()
    atom, idx = targets.resolve_atom(doc, grp, 3)
    assert atom is a3 and idx == 3


def test_resolve_atom_by_numeric_string_treated_as_legacy_index():
    doc, grp, (a1, a2, a3), _ = make_ring()
    atom, idx = targets.resolve_atom(doc, grp, "2")
    assert atom is a2 and idx == 2


def test_resolve_atom_out_of_range_index_raises_value_error():
    doc, grp, _, _ = make_ring()
    with pytest.raises(ValueError):
        targets.resolve_atom(doc, grp, 99)


def test_resolve_atom_unknown_ref_raises_target_not_found():
    doc, grp, _, _ = make_ring()
    with pytest.raises(TargetNotFoundError):
        targets.resolve_atom(doc, grp, "a999")


def test_resolve_bond_by_ref():
    doc, grp, _, (b1, b2) = make_ring()
    bond, idx = targets.resolve_bond(doc, grp, "b102-103")
    assert bond is b2 and idx == 2


def test_resolve_bond_by_legacy_int_index():
    doc, grp, _, (b1, b2) = make_ring()
    bond, idx = targets.resolve_bond(doc, grp, 1)
    assert bond is b1 and idx == 1


def test_refs_survive_membership_reordering():
    """The whole point of a ref over a positional index: it keeps pointing
    at the same atom even after the list it lives in has been rebuilt (e.g.
    by an intervening add_atom/remove call invalidating the cache)."""
    doc, grp, (a1, a2, a3), _ = make_ring()
    cache = {}
    ref = targets.atom_ref(a2)
    targets.unit_atoms_bonds(doc, grp, cache)  # populate cache once

    # Simulate a structural change elsewhere in the unit: remove a3,
    # forcing a rescan next time (unit_signature no longer matches).
    doc.Atoms._items = [a1, a2]
    grp.Objects.Atoms = type("C", (), {"Count": 2})()

    atom, idx = targets.resolve_atom(doc, grp, ref, cache)
    assert atom is a2 and idx == 2, "ref must still resolve to the same atom"
