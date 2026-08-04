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


def test_iter_units_retags_duplicate_group_tag():
    # Simulates copy/paste: ChemDraw tags are Persistent, so a pasted copy
    # of a tagged group carries the SAME claude_id as its source.
    grp1 = FakeUnit(1, atom_count=2, bond_count=1)
    targets.ensure_id(grp1)
    dup = FakeUnit(2, atom_count=2, bond_count=1)
    dup._tags["claude_id"] = grp1._tags["claude_id"]
    assert targets.get_id(dup) == targets.get_id(grp1)  # collision, pre-fix

    doc = FakeDoc(groups=[grp1, dup], atoms=[], bonds=[])
    units = targets.iter_units(doc)

    ids = [targets.get_id(u) for u in units]
    assert len(ids) == len(set(ids)), "every returned unit must have a distinct id"
    assert targets.get_id(grp1) == ids[0], "first-seen keeps its original id"
    assert targets.get_id(dup) != targets.get_id(grp1)


def test_iter_units_retag_is_stable_on_rescan():
    grp1 = FakeUnit(1, atom_count=1, bond_count=0)
    targets.ensure_id(grp1)
    dup = FakeUnit(2, atom_count=1, bond_count=0)
    dup._tags["claude_id"] = grp1._tags["claude_id"]
    doc = FakeDoc(groups=[grp1, dup], atoms=[], bonds=[])

    targets.iter_units(doc)
    fixed_dup_id = targets.get_id(dup)
    targets.iter_units(doc)  # a second, uncached scan must not retag again
    assert targets.get_id(dup) == fixed_dup_id
    assert targets.get_id(grp1) != fixed_dup_id


def test_iter_units_three_way_collision_all_get_distinct_ids():
    grp1 = FakeUnit(1, atom_count=1, bond_count=0)
    targets.ensure_id(grp1)
    shared = grp1._tags["claude_id"].StringValue
    dup_a = FakeUnit(2, atom_count=1, bond_count=0)
    dup_a._tags["claude_id"] = grp1._tags["claude_id"]
    dup_b = FakeUnit(3, atom_count=1, bond_count=0)
    dup_b._tags["claude_id"] = type(grp1._tags["claude_id"])()
    dup_b._tags["claude_id"].StringValue = shared
    doc = FakeDoc(groups=[grp1, dup_a, dup_b], atoms=[], bonds=[])

    units = targets.iter_units(doc)
    ids = [targets.get_id(u) for u in units]
    assert len(ids) == len(set(ids)) == 3


def test_iter_units_retags_duplicate_top_level_fragment_tag():
    # Simulates copy/paste of an ungrouped, hand-drawn structure: no Group
    # wraps either copy, so both are only found via the atom->Fragment
    # walk. A tag collision here must be retagged the same way a
    # colliding Group is above, not silently dropped as "this fragment's
    # tag is inherited from a group we already listed" -- there is no
    # group at all in this scenario, so that read would be wrong and the
    # second structure would vanish from every id-keyed lookup.
    frag1 = FakeUnit(101, atom_count=1, bond_count=0)
    targets.ensure_id(frag1)
    frag2 = FakeUnit(102, atom_count=1, bond_count=0)
    frag2._tags["claude_id"] = frag1._tags["claude_id"]
    atoms = [FakeAtom(frag1), FakeAtom(frag2)]
    doc = FakeDoc(groups=[], atoms=atoms, bonds=[])

    units = targets.iter_units(doc)

    assert len(units) == 2, "both fragments must be returned, not merged"
    ids = [targets.get_id(u) for u in units]
    assert len(ids) == len(set(ids)), "every returned unit must have a distinct id"
    assert targets.get_id(frag1) == ids[0], "first-seen keeps its original id"


def test_iter_units_fragment_covered_by_its_own_group_is_not_retagged():
    # The expected, benign collision: a Data-inserted structure's own
    # internal fragment inherits its owning Group's tag (see module
    # docstring) -- this must still be recognized as "already listed via
    # the group" and skipped, not mistaken for a duplicate top-level
    # fragment and retagged.
    doc, grp = make_doc_with_one_group(atom_count=2, bond_count=1)
    units = targets.iter_units(doc)
    assert len(units) == 1
    assert units[0] is grp
    assert targets.get_id(grp) == targets.get_id(grp)  # unchanged, no retag


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


def make_doc_with_n_groups(n, atom_count=6, bond_count=6):
    """n independent groups, each with its own atoms/bonds tagged to it --
    the target="document" shape bulk_unit_atoms_bonds exists for."""
    groups, all_atoms, all_bonds = [], [], []
    for gid in range(1, n + 1):
        grp = FakeUnit(gid, atom_count=atom_count, bond_count=bond_count)
        targets.ensure_id(grp)
        groups.append(grp)
        all_atoms.extend(FakeAtom(grp) for _ in range(atom_count))
        all_bonds.extend(FakeBond(grp) for _ in range(bond_count))
    doc = FakeDoc(groups=groups, atoms=all_atoms, bonds=all_bonds)
    return doc, groups


# ---------- bulk_unit_atoms_bonds ----------

def test_bulk_unit_atoms_bonds_correctness_matches_per_unit_calls():
    doc, groups = make_doc_with_n_groups(5, atom_count=3, bond_count=2)
    by_uid = targets.bulk_unit_atoms_bonds(doc, groups)
    for g in groups:
        uid = targets.get_id(g)
        atoms, bonds = by_uid[uid]
        assert len(atoms) == 3 and len(bonds) == 2
        assert all(a.Fragment is g for a in atoms)
        assert all(b.Fragment is g for b in bonds)


def test_bulk_unit_atoms_bonds_scans_doc_once_not_once_per_unit():
    n = 20
    doc, groups = make_doc_with_n_groups(n, atom_count=6, bond_count=6)
    targets.bulk_unit_atoms_bonds(doc, groups)
    # ONE pass over doc.Atoms (n*6 items) and ONE over doc.Bonds (n*6 items)
    # -- NOT n passes (which unit_atoms_bonds called once per unit, on an
    # uncached target, would cost: n * n*6 Item() calls, the O(units x
    # atoms) blowup issue #29 is about).
    assert doc.Atoms.item_calls == n * 6
    assert doc.Bonds.item_calls == n * 6


def test_bulk_unit_atoms_bonds_uses_warm_cache_without_rescanning():
    doc, groups = make_doc_with_n_groups(10, atom_count=4, bond_count=3)
    cache = {}
    # Warm the cache for every unit individually first (as e.g. a prior
    # call would have).
    for g in groups:
        targets.unit_atoms_bonds(doc, g, cache)
    calls_before = doc.Atoms.item_calls
    by_uid = targets.bulk_unit_atoms_bonds(doc, groups, cache)
    assert doc.Atoms.item_calls == calls_before, \
        "every unit was already cached -- bulk call must not touch doc.Atoms.Item() at all"
    assert len(by_uid) == 10
    for g in groups:
        atoms, bonds = by_uid[targets.get_id(g)]
        assert len(atoms) == 4 and len(bonds) == 3


def test_bulk_unit_atoms_bonds_mixed_warm_and_cold_units():
    doc, groups = make_doc_with_n_groups(6, atom_count=2, bond_count=1)
    cache = {}
    # Warm only the first 3.
    for g in groups[:3]:
        targets.unit_atoms_bonds(doc, g, cache)
    calls_before = doc.Atoms.item_calls
    by_uid = targets.bulk_unit_atoms_bonds(doc, groups, cache)
    # The cold half still triggers exactly one shared scan (not one per
    # cold unit): total item_calls grows by one doc.Atoms.Count pass.
    assert doc.Atoms.item_calls == calls_before + doc.Atoms.Count
    for g in groups:
        atoms, bonds = by_uid[targets.get_id(g)]
        assert len(atoms) == 2 and len(bonds) == 1


def test_bulk_unit_atoms_bonds_populates_per_unit_cache_for_later_single_calls():
    doc, groups = make_doc_with_n_groups(4, atom_count=2, bond_count=1)
    cache = {}
    targets.bulk_unit_atoms_bonds(doc, groups, cache)
    calls_before = doc.Atoms.item_calls
    # A later single-unit call must hit the cache the bulk call populated,
    # not rescan.
    atoms, bonds = targets.unit_atoms_bonds(doc, groups[0], cache)
    assert len(atoms) == 2 and len(bonds) == 1
    assert doc.Atoms.item_calls == calls_before


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
