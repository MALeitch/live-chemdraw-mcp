"""Stale-cache retry logic for resolve_atom/resolve_bond — pure, no COM.

Verifies the mitigation for a real gap: the doc/unit signature cache is
keyed on plain atom/bond/group COUNTS, so a hand edit that deletes one
atom and draws a new one between two of Claude's calls can net the same
counts and leave a stale cached COM reference in place. Touching that
reference is expected to raise pywintypes.com_error; resolve_atom/
resolve_bond catch that, invalidate the cache, and retry exactly once
(mirroring bridge._StaleHandleMap's precedent for the same class of
problem in contract_functional_groups) before giving up with a clean
TargetNotFoundError instead of letting a raw COM exception leak out.
"""
import pywintypes
import pytest

from chemdraw_connector import targets
from chemdraw_connector.errors import TargetNotFoundError


def _com_error():
    return pywintypes.com_error(-2147417848, "disconnected", None, None)


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


class FlakyOnceAtom:
    """Raises a COM error the first time .ID is read, then works normally —
    simulating a stale reference a fresh rescan recovers from."""

    def __init__(self, id_, fragment):
        self._id = id_
        self.Fragment = fragment
        self._raised = False

    @property
    def ID(self):
        if not self._raised:
            self._raised = True
            raise _com_error()
        return self._id


class AlwaysFlakyAtom:
    """Always raises — simulating a reference that never recovers, even
    after one retry."""

    def __init__(self, id_, fragment):
        self._id = id_
        self.Fragment = fragment

    @property
    def ID(self):
        raise _com_error()


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


def test_resolve_atom_recovers_from_one_stale_touch():
    grp = FakeUnit(1, atom_count=1, bond_count=0)
    targets.ensure_id(grp)
    atom = FlakyOnceAtom(101, grp)
    doc = FakeDoc(groups=[grp], atoms=[atom], bonds=[])
    cache = {}
    targets.unit_atoms_bonds(doc, grp, cache)  # populate the cache once

    resolved, idx = targets.resolve_atom(doc, grp, "a101", cache)
    assert resolved is atom
    assert idx == 1


def test_resolve_atom_raises_clean_error_when_retry_also_fails():
    grp = FakeUnit(1, atom_count=1, bond_count=0)
    targets.ensure_id(grp)
    atom = AlwaysFlakyAtom(101, grp)
    doc = FakeDoc(groups=[grp], atoms=[atom], bonds=[])
    cache = {}
    targets.unit_atoms_bonds(doc, grp, cache)

    with pytest.raises(TargetNotFoundError):
        targets.resolve_atom(doc, grp, "a101", cache)


def test_resolve_atom_without_cache_reraises_com_error_directly():
    grp = FakeUnit(1, atom_count=1, bond_count=0)
    targets.ensure_id(grp)
    atom = AlwaysFlakyAtom(101, grp)
    doc = FakeDoc(groups=[grp], atoms=[atom], bonds=[])

    with pytest.raises(pywintypes.com_error):
        targets.resolve_atom(doc, grp, "a101", cache=None)


def test_resolve_atom_by_legacy_index_also_recovers():
    grp = FakeUnit(1, atom_count=1, bond_count=0)
    targets.ensure_id(grp)
    atom = FlakyOnceAtom(101, grp)
    doc = FakeDoc(groups=[grp], atoms=[atom], bonds=[])
    cache = {}
    targets.unit_atoms_bonds(doc, grp, cache)

    resolved, idx = targets.resolve_atom(doc, grp, 1, cache)
    assert resolved is atom
    assert idx == 1
