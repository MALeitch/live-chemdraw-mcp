"""chemdraw_open_document — Documents.Open() returning None, pure/no COM.

Confirmed live: Application.Documents.Open(path) can return None even when
the open actually landed (the same class of flaky document-returning COM
property this codebase already distrusts everywhere else -- see _doc()'s
own docstring on ActiveDocument). open_document() used to trust that
return value directly and crash with AttributeError on doc.Activate().
These tests verify the re-resolution fallback added in
_document_session._resolve_opened_document instead.
"""
import pytest

from chemdraw_connector.bridge import _document_session as ds
from chemdraw_connector.errors import ChemDrawError


class FakeDoc:
    def __init__(self, name, full_name):
        self.name = name
        self.FullName = full_name
        self.activated = False

    def Activate(self):
        self.activated = True


class FakeDocuments:
    def __init__(self, docs=None):
        self._docs = list(docs or [])
        self.open_return = None      # what .Open() itself returns
        self.append_on_open = None   # FakeDoc appended when .Open() is called

    @property
    def Count(self):
        return len(self._docs)

    def Item(self, i):
        return self._docs[i - 1]

    def Open(self, path):
        if self.append_on_open is not None:
            self._docs.append(self.append_on_open)
        return self.open_return


class FakeApp:
    def __init__(self, documents):
        self.Documents = documents


class FakeConn:
    def __init__(self, app):
        self._app = app
        self.hwnd = 0

    def app(self):
        return self._app


class _FakeSessionBridge(ds._DocumentSession):
    def __init__(self, app):
        self._conn = FakeConn(app)
        self._doc_name = None

    def _run(self, fn, timeout=None):
        return fn()


@pytest.fixture(autouse=True)
def _no_real_nudge(monkeypatch):
    monkeypatch.setattr(ds.nudge, "bring_to_foreground", lambda hwnd: True)


def test_open_document_uses_normal_return_when_open_succeeds(tmp_path):
    path = tmp_path / "real.cdxml"
    path.write_text("<CDXML/>")
    real_doc = FakeDoc("real.cdxml", str(path))
    docs = FakeDocuments(docs=[])
    docs.open_return = real_doc
    bridge = _FakeSessionBridge(FakeApp(docs))

    result = bridge.open_document(str(path))
    assert result["active_document"] == "real.cdxml"
    assert real_doc.activated


def test_open_document_recovers_when_open_returns_none_but_doc_appears(tmp_path):
    path = tmp_path / "real.cdxml"
    path.write_text("<CDXML/>")
    ghost = FakeDoc("real.cdxml", str(path))
    docs = FakeDocuments(docs=[])
    docs.open_return = None            # the crash-causing case
    docs.append_on_open = ghost        # but ChemDraw actually opened it
    bridge = _FakeSessionBridge(FakeApp(docs))

    result = bridge.open_document(str(path))
    assert result["active_document"] == "real.cdxml"
    assert ghost.activated, "must re-resolve and Activate() the real doc, not crash on None"


def test_open_document_falls_back_to_last_item_when_no_name_matches(tmp_path):
    path = tmp_path / "real.cdxml"
    path.write_text("<CDXML/>")
    # Simulates FullName not matching (e.g. a network path quirk) but the
    # Documents count still growing, proving something new did open.
    unnamed = FakeDoc("Document2", "Z:\\some\\other\\path.cdxml")
    docs = FakeDocuments(docs=[])
    docs.open_return = None
    docs.append_on_open = unnamed
    bridge = _FakeSessionBridge(FakeApp(docs))

    result = bridge.open_document(str(path))
    assert result["active_document"] == "Document2"
    assert unnamed.activated


def test_open_document_raises_clean_error_when_nothing_matches(tmp_path):
    path = tmp_path / "real.cdxml"
    path.write_text("<CDXML/>")
    docs = FakeDocuments(docs=[])
    docs.open_return = None
    bridge = _FakeSessionBridge(FakeApp(docs))

    with pytest.raises(ChemDrawError):
        bridge.open_document(str(path))


def test_open_document_rejects_missing_path():
    bridge = _FakeSessionBridge(FakeApp(FakeDocuments(docs=[])))
    with pytest.raises(Exception):
        bridge.open_document("Z:\\definitely\\not\\a\\real\\file.cdxml")
