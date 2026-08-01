"""chemdraw_list_atoms' elements/include_bonds filtering -- pure, no COM.

Regression test for a real pain point hit live: target="document" on a
modest ~250-atom, 7-structure page returned a 60K-character dump (every
atom's x/y/charge/isotope/warning, every bond, for every structure) that
blew the tool result token limit, just to find "which atoms are nitrogen"
for a batch color edit. Filtering server-side (elements=[...]) and
skipping bonds when unneeded (include_bonds=False) fixes that.
"""
import pytest

from chemdraw_connector.bridge import _manipulation as manip


class _Pos:
    def __init__(self, x, y):
        self.X = x
        self.Y = y


class _FakeAtom:
    def __init__(self, element_number, ref):
        self.ElementNumber = element_number
        self.Charge = 0
        self.Isotope = 0
        self.Position = _Pos(1.0, 2.0)
        self.ChemicalWarning = None
        self._ref = ref


class _FakeBond:
    def __init__(self, ref, atom1, atom2):
        self.BondOrder = 1
        self.Atom1 = atom1
        self.Atom2 = atom2
        self.ChemicalWarning = None
        self._ref = ref


class _FakeUnit:
    def __init__(self, uid):
        self.ID = uid


class _StubBridge(manip._Manipulation):
    def __init__(self, doc):
        self._doc_obj = doc

    def _doc(self):
        return self._doc_obj

    def _cache_for(self, doc):
        return {}

    def _run(self, fn, timeout=None):
        return fn()


def _patch_common(monkeypatch, unit_atoms_bonds_map):
    monkeypatch.setattr(manip.targets, "resolve",
                        lambda d, t, c: list(unit_atoms_bonds_map.keys()))
    monkeypatch.setattr(manip.targets, "unit_atoms_bonds",
                        lambda d, u, c: unit_atoms_bonds_map[u])
    monkeypatch.setattr(manip.targets, "ensure_id", lambda u: u.ID)
    monkeypatch.setattr(manip.targets, "atom_ref", lambda a: a._ref)
    monkeypatch.setattr(manip.targets, "bond_ref", lambda b: b._ref)


def test_elements_filter_keeps_only_matching_atoms(monkeypatch):
    unit = _FakeUnit("s1")
    n_atom = _FakeAtom(7, "a1")  # nitrogen
    c_atom = _FakeAtom(6, "a2")  # carbon
    bond = _FakeBond("b1-2", n_atom, c_atom)
    _patch_common(monkeypatch, {unit: ([n_atom, c_atom], [bond])})

    bridge = _StubBridge(doc=object())
    result = bridge.list_atoms_bonds(target="document", elements=["N"])

    assert len(result["structures"]) == 1
    atoms = result["structures"][0]["atoms"]
    assert len(atoms) == 1
    assert atoms[0]["element"] == "N"
    assert atoms[0]["ref"] == "a1"


def test_elements_filter_drops_structure_with_no_matches(monkeypatch):
    unit1 = _FakeUnit("s1")
    unit2 = _FakeUnit("s2")
    c_atom = _FakeAtom(6, "a1")
    n_atom = _FakeAtom(7, "a2")
    _patch_common(monkeypatch, {
        unit1: ([c_atom], []),   # no nitrogen -- should be dropped
        unit2: ([n_atom], []),   # has nitrogen -- should be kept
    })

    bridge = _StubBridge(doc=object())
    result = bridge.list_atoms_bonds(target="document", elements=["N"])

    ids = [s["id"] for s in result["structures"]]
    assert ids == ["s2"]


def test_no_elements_filter_keeps_everything_unfiltered(monkeypatch):
    unit = _FakeUnit("s1")
    c_atom = _FakeAtom(6, "a1")
    _patch_common(monkeypatch, {unit: ([c_atom], [])})

    bridge = _StubBridge(doc=object())
    result = bridge.list_atoms_bonds(target="document")

    assert len(result["structures"]) == 1
    assert len(result["structures"][0]["atoms"]) == 1


def test_include_bonds_false_omits_bonds(monkeypatch):
    unit = _FakeUnit("s1")
    a1, a2 = _FakeAtom(6, "a1"), _FakeAtom(6, "a2")
    bond = _FakeBond("b1-2", a1, a2)
    _patch_common(monkeypatch, {unit: ([a1, a2], [bond])})

    bridge = _StubBridge(doc=object())
    result = bridge.list_atoms_bonds(target="document", include_bonds=False)

    assert result["structures"][0]["bonds"] == []
    assert len(result["structures"][0]["atoms"]) == 2  # atoms unaffected


def test_invalid_element_symbol_raises_before_any_com_work(monkeypatch):
    def _explode(*a, **k):
        raise AssertionError("should never reach targets.resolve")
    monkeypatch.setattr(manip.targets, "resolve", _explode)

    bridge = _StubBridge(doc=object())
    with pytest.raises(ValueError):
        bridge.list_atoms_bonds(target="document", elements=["Zz"])