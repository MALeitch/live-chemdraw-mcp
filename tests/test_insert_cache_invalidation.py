"""chemdraw_insert_structure must not leave a stale per-document unit-list
cache behind — pure, no COM.

Confirmed live: _insert_structure_units() tags new units directly via
targets.ensure_id and never touched the shared per-document cache
(_cache_for), relying entirely on the NEXT query call's own doc_signature
read differing from what was cached before. A query immediately after
insert (chemdraw_get_document_state/chemdraw_status/chemdraw_describe_
canvas) could observe the stale pre-insert unit list for one call. This
test proves the fix: _insert_structure_units now explicitly invalidates
the cache so the very next iter_units call is guaranteed to rescan.
"""
from chemdraw_connector import targets
from chemdraw_connector.bridge import _plumbing as plumbing


class FakeTag:
    def __init__(self):
        self.StringValue = None
        self.Visible = True
        self.Persistent = False


class FakeCollection:
    def __init__(self, items=None):
        self._items = list(items or [])

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]

    def append(self, item):
        self._items.append(item)


class FakeObjects:
    def __init__(self, atom_count=0, bond_count=0):
        self.Atoms = FakeCollection([object()] * atom_count)
        self.Bonds = FakeCollection([object()] * bond_count)


class FakeUnit:
    def __init__(self, uid, left=0, top=0, right=10, bottom=10,
                 atom_count=0, bond_count=0):
        self.ID = uid
        self.Left, self.Top, self.Right, self.Bottom = left, top, right, bottom
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


class FakeDoc:
    def __init__(self, name="doc1"):
        self.Groups = FakeCollection([])
        self.Atoms = FakeCollection([])
        self.Bonds = FakeCollection([])
        self.Objects = None  # not touched: _insert_raw is stubbed in tests
        self.name = name


class _FakeInsertBridge(plumbing._Plumbing):
    def __init__(self):
        self._caches = {}
        self._simulate_insert = None  # callable(doc) -> mutates doc

    def _insert_raw(self, objs, mime, payload):
        if self._simulate_insert is not None:
            self._simulate_insert()


def test_insert_structure_units_invalidates_cache_so_next_scan_sees_new_unit():
    doc = FakeDoc()
    bridge = _FakeInsertBridge()
    cache = bridge._cache_for(doc)

    # Prime the cache with the empty pre-insert state, as a caller would
    # have from an earlier query (e.g. chemdraw_get_document_state) before
    # this insert happens.
    before = targets.iter_units(doc, cache)
    assert before == []

    new_group = FakeUnit(1, atom_count=1, bond_count=0)

    def _simulate():
        doc.Groups.append(new_group)
        doc.Atoms.append(FakeAtom(new_group))

    bridge._simulate_insert = _simulate

    units = bridge._insert_structure_units(doc, "CCO", "smiles")
    assert len(units) == 1

    # The cache must be invalidated, not left holding the pre-insert
    # (now-stale) empty list under whatever signature happened to be
    # cached before.
    assert cache["doc_sig"] is None
    assert cache["units"] is None

    after = targets.iter_units(doc, cache)
    assert len(after) == 1, \
        "a query right after insert must see the new unit, not a stale cache"


def test_insert_structure_units_invalidation_is_scoped_to_its_own_document():
    # Two documents' caches must stay independent -- inserting into one
    # must not disturb the other's already-cached (and still valid) state.
    doc_a, doc_b = FakeDoc(name="doc_a"), FakeDoc(name="doc_b")
    bridge = _FakeInsertBridge()
    cache_a = bridge._cache_for(doc_a)
    cache_b = bridge._cache_for(doc_b)

    existing_b_group = FakeUnit(1, atom_count=1, bond_count=0)
    doc_b.Groups.append(existing_b_group)
    doc_b.Atoms.append(FakeAtom(existing_b_group))
    targets.iter_units(doc_b, cache_b)  # prime doc_b's cache with 1 unit
    assert cache_b["units"] is not None

    new_group = FakeUnit(2, atom_count=1, bond_count=0)

    def _simulate():
        doc_a.Groups.append(new_group)
        doc_a.Atoms.append(FakeAtom(new_group))

    bridge._simulate_insert = _simulate
    bridge._insert_structure_units(doc_a, "CCO", "smiles")

    assert cache_a["doc_sig"] is None, "doc_a's cache must be invalidated"
    assert cache_b["units"] is not None, \
        "doc_b's already-primed cache must be untouched by doc_a's insert"
