"""targets.resolve('selection'/'document') and find_by_id — pure, no COM.
Self-contained fakes, not shared with other test_targets_*.py files (tests/
isn't a package, no relative imports)."""
import pytest

from chemdraw_connector import targets
from chemdraw_connector.errors import NothingSelectedError, TargetNotFoundError


class FakeTag:
    def __init__(self):
        self.StringValue = None


class FakeObjects:
    def __init__(self, atom_count=0, bond_count=0):
        self.Atoms = type("C", (), {"Count": atom_count})()
        self.Bonds = type("C", (), {"Count": bond_count})()


class FakeUnit:
    def __init__(self, uid, atom_count=0, bond_count=0):
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
    def __init__(self, id_, fragment, selected=False):
        self.ID = id_
        self.Fragment = fragment
        self.Selected = selected


class Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class FakeSelection:
    def __init__(self, groups):
        self.Groups = Coll(groups)


class FakeDoc:
    def __init__(self, groups, atoms, bonds, selection=None, name="doc1"):
        self.Groups = Coll(groups)
        self.Atoms = Coll(atoms)
        self.Bonds = Coll(bonds)
        self.Selection = selection
        self.name = name


# ---------- find_by_id ----------

def test_find_by_id_none_raises_instead_of_matching_untagged_unit():
    """A never-tagged unit's get_id() returns None; find_by_id(doc, None)
    must not silently treat that None==None as a match."""
    untagged = FakeUnit(1)  # never tagged
    doc = FakeDoc(groups=[untagged], atoms=[], bonds=[])
    with pytest.raises(TargetNotFoundError):
        targets.find_by_id(doc, None)


def test_find_by_id_empty_string_raises():
    doc = FakeDoc(groups=[], atoms=[], bonds=[])
    with pytest.raises(TargetNotFoundError):
        targets.find_by_id(doc, "")


def test_find_by_id_still_finds_real_id():
    grp = FakeUnit(1)
    oid = targets.ensure_id(grp)
    doc = FakeDoc(groups=[grp], atoms=[], bonds=[])
    assert targets.find_by_id(doc, oid) is grp


# ---------- resolve("selection") ----------

def test_resolve_selection_finds_selected_group():
    grp = FakeUnit(1, atom_count=1, bond_count=0)
    targets.ensure_id(grp)
    doc = FakeDoc(groups=[grp], atoms=[], bonds=[],
                  selection=FakeSelection(groups=[grp]))
    units = targets.resolve(doc, "selection")
    assert units == [grp]


def test_resolve_selection_finds_selected_ungrouped_fragment():
    """A hand-drawn structure that never became a Group has no entry in
    sel.Groups; resolve('selection') must still find it via a selected
    atom, the same way iter_units treats it as an addressable unit."""
    frag = FakeUnit(1, atom_count=1, bond_count=0)  # untagged, not a Group
    atom = FakeAtom(101, frag, selected=True)
    doc = FakeDoc(groups=[], atoms=[atom], bonds=[],
                  selection=FakeSelection(groups=[]))
    units = targets.resolve(doc, "selection")
    assert units == [frag]


def test_resolve_selection_does_not_duplicate_group_already_found():
    grp = FakeUnit(1, atom_count=1, bond_count=0)
    targets.ensure_id(grp)
    atom = FakeAtom(101, grp, selected=True)
    doc = FakeDoc(groups=[grp], atoms=[atom], bonds=[],
                  selection=FakeSelection(groups=[grp]))
    units = targets.resolve(doc, "selection")
    assert units == [grp]


def test_resolve_selection_raises_when_nothing_selected():
    doc = FakeDoc(groups=[], atoms=[], bonds=[], selection=FakeSelection(groups=[]))
    with pytest.raises(NothingSelectedError):
        targets.resolve(doc, "selection")
