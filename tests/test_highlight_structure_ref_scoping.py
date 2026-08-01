"""highlight_structure's per-entry atom_refs/bond_refs -> atom_ids/
bond_pairs scoping -- pure, no COM (COM machinery stubbed out).

Regression test for a real bug: an entry with atom_refs but no bond_refs
(or vice versa) used to pass None for the omitted side, which
domain.highlight_cdxml's set_highlight/clear_highlight treat as "match
everything" -- so a rainbow-gradient call (many single-atom and
single-bond entries in one call) had every atom-only entry ALSO recolor
all bonds, and every bond-only entry ALSO recolor all atoms, silently
wiping out earlier entries' colors. Confirmed live before this fix:
every entry's reported highlighted_atoms/highlighted_bonds count showed
"1 and 20" or "19 and 1" instead of the intended single-digit counts.
Fixed by only defaulting to None/None (the real "whole structure" case)
when BOTH refs are omitted; a partial entry now defaults the omitted
side to [] (match nothing) instead.
"""
from chemdraw_connector.bridge import _highlight as highlight_mod


class _FakeUnit:
    def __init__(self, uid):
        self.ID = uid
        self.Left = 10.0
        self.Top = 20.0


class _FakeObjects:
    def __init__(self):
        self.cleared = False

    def GetData(self, mime):
        return "<fragment/>"

    def Clear(self):
        self.cleared = True

    def Move(self, dx, dy):
        pass


class _FakeDoc:
    def __init__(self):
        self.Atoms = type("A", (), {"Count": 5})()
        self.Groups = _FakeGroups()
        self.Objects = _FakeObjects()


class _FakeGroups:
    def __init__(self):
        self.Count = 1
        self._new_unit = _FakeUnit("new")

    def Item(self, i):
        return self._new_unit


class _StubBridge(highlight_mod._Highlight):
    def __init__(self, doc):
        self._doc_obj = doc

    def _doc(self):
        return self._doc_obj

    def _cache_for(self, doc):
        return {}

    def _run(self, fn, timeout=None):
        return fn()

    def _maybe_snapshot(self, doc):
        return "backup.cdxml"

    def _insert_raw(self, objs, mime, payload):
        # Simulate a successful reimport: exactly one new group appears.
        self._doc_obj.Groups.Count += 1


def _patch_common(monkeypatch, target_unit):
    monkeypatch.setattr(highlight_mod.targets, "resolve", lambda d, t, c: [target_unit])
    monkeypatch.setattr(highlight_mod.targets, "unit_objects", lambda u: _FakeObjects())
    monkeypatch.setattr(highlight_mod.targets, "ensure_id", lambda u: "claude-result")
    monkeypatch.setattr(highlight_mod.targets, "_invalidate_cache", lambda c: None)
    monkeypatch.setattr(highlight_mod.hc, "parse", lambda text: "PARSED_ROOT")
    monkeypatch.setattr(highlight_mod.hc, "serialize", lambda root: "<CDXML/>")
    monkeypatch.setattr(highlight_mod.hc, "resolve_color_index", lambda root, color: 4)


def test_atom_only_entry_does_not_touch_all_bonds(monkeypatch):
    calls = []

    def fake_set_highlight(root, index, atom_ids, bond_pairs):
        calls.append((atom_ids, bond_pairs))
        return (len(atom_ids or []), len(bond_pairs or []))

    monkeypatch.setattr(highlight_mod.hc, "set_highlight", fake_set_highlight)
    _patch_common(monkeypatch, _FakeUnit("s1"))

    bridge = _StubBridge(_FakeDoc())
    bridge.highlight_structure("claude-s1", [{"color": "#FF0000", "atom_refs": ["a1", "a2"]}])

    atom_ids, bond_pairs = calls[0]
    assert atom_ids == [1, 2]
    assert bond_pairs == []  # NOT None -- must not mean "all bonds"


def test_bond_only_entry_does_not_touch_all_atoms(monkeypatch):
    calls = []

    def fake_set_highlight(root, index, atom_ids, bond_pairs):
        calls.append((atom_ids, bond_pairs))
        return (len(atom_ids or []), len(bond_pairs or []))

    monkeypatch.setattr(highlight_mod.hc, "set_highlight", fake_set_highlight)
    _patch_common(monkeypatch, _FakeUnit("s1"))

    bridge = _StubBridge(_FakeDoc())
    bridge.highlight_structure("claude-s1", [{"color": "#FF0000", "bond_refs": ["b1-2"]}])

    atom_ids, bond_pairs = calls[0]
    assert atom_ids == []  # NOT None -- must not mean "all atoms"
    assert bond_pairs == [(1, 2)]


def test_both_omitted_means_whole_structure(monkeypatch):
    calls = []

    def fake_set_highlight(root, index, atom_ids, bond_pairs):
        calls.append((atom_ids, bond_pairs))
        return (0, 0)

    monkeypatch.setattr(highlight_mod.hc, "set_highlight", fake_set_highlight)
    _patch_common(monkeypatch, _FakeUnit("s1"))

    bridge = _StubBridge(_FakeDoc())
    bridge.highlight_structure("claude-s1", [{"color": "#FF0000"}])

    atom_ids, bond_pairs = calls[0]
    assert atom_ids is None  # None IS "match everything" for this real case
    assert bond_pairs is None


def test_multi_entry_rainbow_does_not_clobber_earlier_entries(monkeypatch):
    """The actual bug scenario: several atom-only and bond-only entries
    in one call. Each must only touch what it names."""
    calls = []

    def fake_set_highlight(root, index, atom_ids, bond_pairs):
        calls.append((atom_ids, bond_pairs))
        return (len(atom_ids or []), len(bond_pairs or []))

    monkeypatch.setattr(highlight_mod.hc, "set_highlight", fake_set_highlight)
    _patch_common(monkeypatch, _FakeUnit("s1"))

    bridge = _StubBridge(_FakeDoc())
    bridge.highlight_structure("claude-s1", [
        {"color": "#FF0000", "atom_refs": ["a1"]},
        {"color": "#00FF00", "atom_refs": ["a2"]},
        {"color": "#0000FF", "bond_refs": ["b1-2"]},
    ])

    assert calls[0] == ([1], [])
    assert calls[1] == ([2], [])
    assert calls[2] == ([], [(1, 2)])
