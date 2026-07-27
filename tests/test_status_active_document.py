"""chemdraw_status must not silently blank its structures/captions/boxes
block when app.ActiveDocument is flaky-None with 2+ documents open — pure,
no COM.

Confirmed live gap: status() read app.ActiveDocument directly and only
fell back `if Documents.Count == 1`. Since this connector deliberately
keeps a scratch document open alongside the user's real one
(use_scratch_document), 2+ documents open is the common case, not the
exception -- so that fallback rarely fired, and a flaky ActiveDocument
silently omitted structures_on_page/captions_on_page/boxes_on_page with no
error. The fix extends _active_document_name with a tracked-name fallback
tier and routes status()'s own `doc` resolution through the same name.
"""
from chemdraw_connector.bridge import _document_session as ds
from chemdraw_connector.bridge import _plumbing as plumbing


class FakeCaptions:
    Count = 0


class FakeDoc:
    def __init__(self, name):
        self.name = name
        self.Captions = FakeCaptions()
        self.NumChemicalWarnings = 0


class FakeCollection:
    def __init__(self, items):
        self._items = items

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class FakeApp:
    def __init__(self, docs, active=None):
        self.Documents = FakeCollection(docs)
        self.ActiveDocument = active  # simulates the confirmed-flaky read


class FakeConn:
    def __init__(self, app):
        self._app = app
        self.hwnd = 0

    def app(self):
        return self._app

    def info(self):
        return {}


class _FakeStatusBridge(plumbing._Plumbing, ds._DocumentSession):
    # Mixes in the real _Plumbing so _active_document_name (the method
    # under test, along with status()'s own use of it) runs unmodified —
    # matches how ChemDrawBridge itself composes _Plumbing + _DocumentSession.
    def __init__(self, app, tracked_name):
        self._conn = FakeConn(app)
        self._doc_name = tracked_name
        self._caches = {}

    def _run(self, fn, timeout=None):
        return fn()

    def _graphics_boxes(self, doc):
        return []


def test_status_resolves_tracked_document_when_active_document_is_flaky_with_two_docs_open(monkeypatch):
    scratch = FakeDoc("chemdraw-mcp-scratch.cdxml")
    real = FakeDoc("my-real-doc.cdxml")
    app = FakeApp([scratch, real], active=None)
    bridge = _FakeStatusBridge(app, tracked_name="my-real-doc.cdxml")

    monkeypatch.setattr(ds.state, "build_snapshot", lambda doc, cache: [
        {"bounds": {"left": 0, "top": 0, "right": 10, "bottom": 10}}])
    monkeypatch.setattr(ds.canvas, "classify_units",
                        lambda snap: (snap, {}, {}, []))

    info = bridge.status()

    assert info["open_documents"] == 2
    assert info["active_document"] == "my-real-doc.cdxml"
    assert "structures_on_page" in info, \
        "with 2+ docs open and a flaky ActiveDocument, the tracked document must still be resolved"
    assert info["structures_on_page"] == 1


def test_status_reports_none_active_document_when_nothing_tracked_and_multiple_open(monkeypatch):
    # No tracked name yet (e.g. a fresh connector session that never
    # mutated a document), ActiveDocument flaky-None, 2+ docs open -- there
    # really is no reasonable single guess, so this must stay None rather
    # than silently picking one, same as before the fix.
    doc_a = FakeDoc("a.cdxml")
    doc_b = FakeDoc("b.cdxml")
    app = FakeApp([doc_a, doc_b], active=None)
    bridge = _FakeStatusBridge(app, tracked_name=None)

    info = bridge.status()

    assert info["open_documents"] == 2
    assert info["active_document"] is None
    assert "structures_on_page" not in info


def test_status_uses_active_document_directly_when_it_answers(monkeypatch):
    doc = FakeDoc("only-doc.cdxml")
    app = FakeApp([doc], active=doc)
    bridge = _FakeStatusBridge(app, tracked_name=None)

    monkeypatch.setattr(ds.state, "build_snapshot", lambda doc, cache: [])
    monkeypatch.setattr(ds.canvas, "classify_units",
                        lambda snap: (snap, {}, {}, []))

    info = bridge.status()
    assert info["active_document"] == "only-doc.cdxml"
    assert info["structures_on_page"] == 0
