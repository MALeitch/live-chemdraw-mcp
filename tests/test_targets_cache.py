"""Cache correctness for targets.iter_units / unit_atoms_bonds — pure,
no COM. Uses minimal fake stand-ins for the COM collections/objects
targets.py touches, instrumented to count Item() calls so tests can prove
a cache hit skipped the full scan, not just that it returned the right
answer."""
from chemdraw_connector import targets


class FakeTag:
    def __init__(self):
        self.StringValue = None
        self.Visible = True
        self.Persistent = False


class FakeCollection:
    """Stands in for an IChemDrawObjects-style collection: 1-based Item(),
    Count, and an item_calls counter so tests can detect a rescan."""

    def __init__(self, items=None):
        self._items = list(items or [])
        self.item_calls = 0

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        self.item_calls += 1
        return self._items[i - 1]

    def append(self, item):
        self._items.append(item)


class FakeObjects:
    """unit.Objects: exposes Atoms/Bonds sub-collections' .Count only, the
    only unit-scoped access targets.py ever makes (per its .Item() crash
    warning)."""

    def __init__(self, atom_count=0, bond_count=0):
        self.Atoms = FakeCollection([object()] * atom_count)
        self.Bonds = FakeCollection([object()] * bond_count)


class FakeUnit:
    """Stands in for a Group or Fragment: doc-unique ID plus the
    GetObjectTag/MakeObjectTag pair targets.ensure_id relies on."""

    def __init__(self, uid, atom_count=0, bond_count=0):
        self.ID = uid
        self.Objects = FakeObjects(atom_count, bond_count)
        self._tags = {}

    def GetObjectTag(self, name):
        return self._tags.get(name)

    def MakeObjectTag(self, name, _persistent_flag):
        tag = FakeTag()
        self._tags[name] = tag
        return tag


class FakeAtom:
    def __init__(self, fragment):
        self.Fragment = fragment


class FakeBond:
    def __init__(self, fragment):
        self.Fragment = fragment


class FakeDoc:
    def __init__(self, groups=None, atoms=None, bonds=None, name="doc1"):
        self.Groups = FakeCollection(groups or [])
        self.Atoms = FakeCollection(atoms or [])
        self.Bonds = FakeCollection(bonds or [])
        self.name = name


def make_doc_with_one_group(atom_count=2, bond_count=1):
    grp = FakeUnit(1, atom_count=atom_count, bond_count=bond_count)
    # Tag it up front, as real usage would (e.g. insert_structure tags new
    # units immediately) — iter_units only dedupes a group against its own
    # atoms'-fragment entries via a tag both share, per its module docstring.
    targets.ensure_id(grp)
    atoms = [FakeAtom(grp) for _ in range(atom_count)]
    bonds = [FakeBond(grp) for _ in range(bond_count)]
    doc = FakeDoc(groups=[grp], atoms=atoms, bonds=bonds)
    return doc, grp


# ---------- iter_units ----------

def test_iter_units_without_cache_always_rescans():
    doc, grp = make_doc_with_one_group()
    first = targets.iter_units(doc)
    calls_after_first = doc.Groups.item_calls
    second = targets.iter_units(doc)
    assert second is not first
    assert doc.Groups.item_calls > calls_after_first


def test_iter_units_cache_hit_skips_rescan_when_unchanged():
    doc, grp = make_doc_with_one_group()
    cache = {}
    first = targets.iter_units(doc, cache)
    calls_after_first = doc.Groups.item_calls
    second = targets.iter_units(doc, cache)
    assert second is first, "cache hit should return the same cached list object"
    assert doc.Groups.item_calls == calls_after_first, \
        "cache hit must not touch Groups.Item() again"


def test_iter_units_rescans_after_hand_edit_changes_doc_signature():
    doc, grp = make_doc_with_one_group()
    cache = {}
    first = targets.iter_units(doc, cache)
    # Simulate a hand edit: a second group appears on the canvas, changing
    # doc.Groups.Count — the signature check must catch this even though
    # nothing routed through our own mutating calls.
    grp2 = FakeUnit(2, atom_count=1, bond_count=0)
    doc.Groups.append(grp2)
    second = targets.iter_units(doc, cache)
    assert second is not first
    assert len(second) == 2


def test_iter_units_cache_stays_correct_across_multiple_docs():
    doc_a, grp_a = make_doc_with_one_group(atom_count=2)
    doc_b, grp_b = make_doc_with_one_group(atom_count=3)
    doc_b.name = "doc2"
    cache_a, cache_b = {}, {}
    units_a = targets.iter_units(doc_a, cache_a)
    units_b = targets.iter_units(doc_b, cache_b)
    assert targets.get_id(units_a[0]) != targets.get_id(units_b[0]) or \
        units_a[0] is not units_b[0]
    assert len(units_a) == 1 and len(units_b) == 1


# ---------- unit_atoms_bonds ----------

def test_unit_atoms_bonds_cache_hit_skips_rescan_when_unchanged():
    doc, grp = make_doc_with_one_group(atom_count=3, bond_count=2)
    cache = {}
    atoms1, bonds1 = targets.unit_atoms_bonds(doc, grp, cache)
    calls_after_first = doc.Atoms.item_calls
    atoms2, bonds2 = targets.unit_atoms_bonds(doc, grp, cache)
    assert atoms2 is atoms1 and bonds2 is bonds1
    assert doc.Atoms.item_calls == calls_after_first, \
        "cache hit must not touch doc.Atoms.Item() again"


def test_unit_atoms_bonds_rescans_after_unit_signature_changes():
    doc, grp = make_doc_with_one_group(atom_count=2, bond_count=1)
    cache = {}
    atoms1, bonds1 = targets.unit_atoms_bonds(doc, grp, cache)
    assert len(atoms1) == 2

    # Simulate growing the structure by one atom (e.g. add_atom): both the
    # unit's own Objects.Atoms.Count and doc.Atoms must reflect it for this
    # to be a realistic hand-edit-equivalent mutation.
    new_atom = FakeAtom(grp)
    doc.Atoms.append(new_atom)
    grp.Objects.Atoms.append(object())

    atoms2, bonds2 = targets.unit_atoms_bonds(doc, grp, cache)
    assert len(atoms2) == 3
    assert atoms2 is not atoms1


def test_doc_level_rescan_clears_atom_bond_cache():
    doc, grp = make_doc_with_one_group(atom_count=2, bond_count=1)
    cache = {}
    targets.iter_units(doc, cache)
    targets.unit_atoms_bonds(doc, grp, cache)
    assert cache["atom_bond"]  # populated

    grp2 = FakeUnit(2, atom_count=1, bond_count=0)
    doc.Groups.append(grp2)
    targets.iter_units(doc, cache)  # doc signature changed -> full rescan
    assert cache["atom_bond"] == {}, \
        "a doc-level change must invalidate all cached per-unit atom/bond data"


def test_unit_without_objects_is_never_cached():
    class NoObjectsUnit:
        ID = 99
        def GetObjectTag(self, name):
            return None
        def MakeObjectTag(self, name, _flag):
            return FakeTag()

    unit = NoObjectsUnit()
    doc = FakeDoc(groups=[], atoms=[], bonds=[])
    cache = {}
    atoms, bonds = targets.unit_atoms_bonds(doc, unit, cache)
    assert atoms == [] and bonds == []
    assert cache.get("atom_bond", {}) == {}, \
        "a unit with no usable .Objects must never be cached (unit_signature fails)"
