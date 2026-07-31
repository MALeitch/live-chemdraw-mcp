"""export_image's write-guard ordering -- pure, no COM.

Regression for DEBUG_REPORT.md L-1 (2026-07-30, fixed 2026-07-31): the
overwrite guard used to run AFTER the multi-id-target branch mutated the
live document's selection (doc.Objects.Unselect() + setting .Selected on
each unit) and after the expensive GetData render -- so a call the guard
went on to refuse had already replaced whatever the user had selected in
ChemDraw, a mutation performed by a call that reports failure. Fixed by
hoisting the guard to the top of go(), before doc.Objects/targets.resolve
are touched at all.
"""
import pytest

from chemdraw_connector.bridge import _structure_io as structio
from chemdraw_connector.errors import InvalidInputError


class _FakeUnit:
    def __init__(self, uid):
        self.ID = uid
        self.Selected = False


class _FakeObjectsCollection:
    def __init__(self):
        self.unselect_called = False
        self.get_data_called = False

    def Unselect(self):
        self.unselect_called = True

    def GetData(self, mime, dpi):
        self.get_data_called = True
        return b"PNGDATA"


class _FakeDoc:
    def __init__(self):
        self.Objects = _FakeObjectsCollection()
        self.Selection = type("S", (), {"Objects": _FakeObjectsCollection()})()


class _StubBridge(structio._StructureIO):
    def __init__(self, doc):
        self._doc_obj = doc

    def _doc(self):
        return self._doc_obj

    def _cache_for(self, doc):
        return {}

    def _run(self, fn, timeout=None):
        return fn()

    @staticmethod
    def _guard_write_path(path, overwrite):
        from chemdraw_connector.bridge._plumbing import _Plumbing
        return _Plumbing._guard_write_path(path, overwrite)


def test_refused_write_never_touches_selection_or_renders(tmp_path, monkeypatch):
    existing = tmp_path / "out.png"
    existing.write_bytes(b"already here")

    doc = _FakeDoc()
    units = [_FakeUnit("a"), _FakeUnit("b")]
    bridge = _StubBridge(doc)

    monkeypatch.setattr(structio.targets, "resolve", lambda d, t, c: units)

    with pytest.raises(InvalidInputError, match="already exists"):
        bridge.export_image(target=["a", "b"], path=str(existing), overwrite=False)

    assert doc.Objects.unselect_called is False
    assert doc.Objects.get_data_called is False
    assert all(u.Selected is False for u in units)
    assert existing.read_bytes() == b"already here"  # untouched


def test_successful_write_still_selects_and_renders(tmp_path, monkeypatch):
    target_path = tmp_path / "new.png"

    doc = _FakeDoc()
    units = [_FakeUnit("a"), _FakeUnit("b")]
    bridge = _StubBridge(doc)

    monkeypatch.setattr(structio.targets, "resolve", lambda d, t, c: units)
    monkeypatch.setattr(structio.t, "mime_for", lambda fmt: "image/png")

    result = bridge.export_image(target=["a", "b"], path=str(target_path))

    assert doc.Objects.unselect_called is True
    assert all(u.Selected for u in units)
    assert doc.Selection.Objects.get_data_called is True  # multi-unit -> renders via Selection.Objects
    assert target_path.read_bytes() == b"PNGDATA"
    assert result["bytes"] == len(b"PNGDATA")
