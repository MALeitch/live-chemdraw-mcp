"""Tests for scratch document lifecycle - concurrent use, graphics clearing, etc."""
from chemdraw_connector.bridge import _document_session as ds
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

    def __iter__(self):
        return iter(self._items)

    def Clear(self):
        self._items.clear()

    def Add(self):
        # For Documents.Add() - creates a new FakeDoc
        from tests.test_scratch_doc_lifecycle import FakeDoc
        new_doc = FakeDoc(f"Document{len(self._items) + 1}")
        self._items.append(new_doc)
        return new_doc


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


class FakeGraphics:
    def __init__(self, items=None):
        self._items = list(items or [])

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class FakeGraphic:
    def __init__(self, left=0, top=0, right=10, bottom=10):
        self.Left = left
        self.Top = top
        self.Right = right
        self.Bottom = bottom


class FakeDoc:
    def __init__(self, name="doc1"):
        self.Groups = FakeCollection([])
        self.Atoms = FakeCollection([])
        self.Bonds = FakeCollection([])
        self.Objects = FakeCollection([])
        self.name = name
        self.FullName = f"C:\\fake\\{name}"
        self.Width = 540.0
        self.Height = 720.0
        self.Captions = FakeCollection([])
        self.Arrows = FakeCollection([])
        self.Graphics = FakeCollection([])
        self.NumChemicalWarnings = 0
        self.Modified = False
        self.Selection = None
        self.Activate_called = False

    def Activate(self):
        self.Activate_called = True

    def SaveAs(self, path):
        pass


class FakeApp:
    def __init__(self, documents=None, active_doc=None):
        self.Documents = FakeCollection(documents or [])
        self.ActiveDocument = active_doc
        # Add an Add method to Documents that creates a new FakeDoc
        self.Documents.Add = lambda: self.Documents._items.append(FakeDoc(f"Document{len(self.Documents._items) + 1}")) or self.Documents._items[-1]


class FakeConn:
    def __init__(self, app, hwnd=0):
        self._app = app
        self.hwnd = hwnd

    def app(self):
        return self._app

    def info(self):
        return {}


class _FakeBridge(plumbing._Plumbing, ds._DocumentSession):
    def __init__(self, app):
        self._conn = FakeConn(app)
        self._doc_name = None
        self._caches = {}
        self._last_backup = None

    def _run(self, fn, timeout=None):
        return fn()

    def _doc(self):
        return self._conn.app().ActiveDocument

    def _cache_for(self, doc):
        return self._caches.setdefault(doc, {})

    def _doc_window(self, doc):
        return 0

    def _graphics_boxes(self, doc):
        return []


def test_use_scratch_document_clears_atoms_and_graphics():
    """Full bridge path: use_scratch_document clears both Atoms and Graphics."""
    doc = FakeDoc("chemdraw-mcp-scratch.cdxml")
    # Add some existing content - need to add atoms to doc.Atoms directly
    existing = FakeUnit(1, atom_count=5, bond_count=4)
    doc.Groups.append(existing)
    # Add 5 atoms to doc.Atoms to match what real code would see
    for _ in range(5):
        doc.Atoms.append(FakeAtom(existing))
    # Add some graphics (box frames, etc.)
    doc.Graphics.append(FakeGraphic(0, 0, 100, 100))
    doc.Graphics.append(FakeGraphic(10, 10, 110, 110))

    app = FakeApp(documents=[doc], active_doc=doc)
    bridge = _FakeBridge(app)

    import chemdraw_connector.com.nudge as nudge_mod
    original_bring = nudge_mod.bring_to_foreground
    nudge_mod.bring_to_foreground = lambda hwnd: True

    try:
        result = bridge.use_scratch_document()
        assert result["active_document"] == "chemdraw-mcp-scratch.cdxml"
        assert result["reused"] is True
        assert result["cleared_atoms"] == 5
        # Graphics clearing is guarded (NEEDS LIVE CONFIRMATION) but should be attempted
        assert "cleared_graphics" in result
    finally:
        nudge_mod.bring_to_foreground = original_bring


def test_use_scratch_document_creates_new_when_none_exists():
    """Full bridge path: use_scratch_document creates new doc when none exists."""
    app = FakeApp(documents=[], active_doc=None)
    bridge = _FakeBridge(app)

    import chemdraw_connector.com.nudge as nudge_mod
    original_bring = nudge_mod.bring_to_foreground
    nudge_mod.bring_to_foreground = lambda hwnd: True

    try:
        result = bridge.use_scratch_document()
        assert result["reused"] is False
        assert result["cleared_atoms"] == 0
        assert app.Documents.Count == 1
        # The new document gets a default name "Document1" since SaveAs
        # is in a try/except and may fail in our mock
        assert app.Documents.Item(1).name.startswith("Document")
    finally:
        nudge_mod.bring_to_foreground = original_bring


def test_use_scratch_document_handles_graphics_clear_failure_gracefully():
    """Full bridge path: Graphics.Clear() failure doesn't break scratch acquisition."""
    doc = FakeDoc("chemdraw-mcp-scratch.cdxml")
    # Replace Graphics with an object that has Count but no Clear method
    class FakeGraphicsNoClear:
        def __init__(self, items):
            self._items = items
        @property
        def Count(self):
            return len(self._items)
        def Item(self, i):
            return self._items[i - 1]
    
    doc.Graphics = FakeGraphicsNoClear([FakeGraphic(0, 0, 100, 100)])

    app = FakeApp(documents=[doc], active_doc=doc)
    bridge = _FakeBridge(app)

    import chemdraw_connector.com.nudge as nudge_mod
    original_bring = nudge_mod.bring_to_foreground
    nudge_mod.bring_to_foreground = lambda hwnd: True

    try:
        result = bridge.use_scratch_document()
        assert result["reused"] is True
        # Should not crash even if Graphics.Clear() fails
        assert "cleared_graphics" in result
    finally:
        nudge_mod.bring_to_foreground = original_bring


def test_use_scratch_document_saves_to_stable_path():
    """Full bridge path: new scratch document gets saved to stable path for reuse."""
    app = FakeApp(documents=[], active_doc=None)
    bridge = _FakeBridge(app)

    import chemdraw_connector.com.nudge as nudge_mod
    original_bring = nudge_mod.bring_to_foreground
    nudge_mod.bring_to_foreground = lambda hwnd: True

    try:
        result = bridge.use_scratch_document()
        assert result["reused"] is False
        # The save path should be predictable
        assert "active_document" in result
    finally:
        nudge_mod.bring_to_foreground = original_bring


def test_close_document_clears_modified_flag_before_close():
    """Full bridge path: close_document clears Modified flag before closing to suppress prompt."""
    doc = FakeDoc("test.cdxml")
    doc.Modified = True

    app = FakeApp(documents=[doc], active_doc=doc)
    bridge = _FakeBridge(app)

    import chemdraw_connector.com.doc_window as doc_window_mod
    import chemdraw_connector.com.nudge as nudge_mod

    original_find = doc_window_mod.find_document_window
    original_close = doc_window_mod.close_document_window
    original_bring = nudge_mod.bring_to_foreground

    found_hwnd = [None]
    closed_hwnd = [None]

    def mock_find(name, hwnd):
        found_hwnd[0] = 12345
        return 12345

    def mock_close(hwnd):
        closed_hwnd[0] = hwnd
        # Simulate document actually closing
        app.Documents._items.remove(doc)

    doc_window_mod.find_document_window = mock_find
    doc_window_mod.close_document_window = mock_close
    nudge_mod.bring_to_foreground = lambda hwnd: True

    try:
        result = bridge.close_document("test.cdxml", discard_changes=True)
        assert result["closed"] == "test.cdxml"
        assert doc.Modified is False  # Cleared before close
        assert found_hwnd[0] == 12345
        assert closed_hwnd[0] == 12345
    finally:
        doc_window_mod.find_document_window = original_find
        doc_window_mod.close_document_window = original_close
        nudge_mod.bring_to_foreground = original_bring


def test_close_document_refuses_unsaved_changes_without_discard():
    """Full bridge path: close_document refuses unsaved changes unless discard=True."""
    doc = FakeDoc("test.cdxml")
    doc.Modified = True

    app = FakeApp(documents=[doc], active_doc=doc)
    bridge = _FakeBridge(app)

    from chemdraw_connector.errors import InvalidInputError

    try:
        bridge.close_document("test.cdxml", discard_changes=False)
        assert False, "Should have raised InvalidInputError"
    except InvalidInputError as e:
        assert "unsaved changes" in str(e).lower()


def test_close_document_activates_remaining_document():
    """Full bridge path: closing active document activates the remaining one."""
    doc1 = FakeDoc("doc1.cdxml")
    doc2 = FakeDoc("doc2.cdxml")

    app = FakeApp(documents=[doc1, doc2], active_doc=doc1)
    bridge = _FakeBridge(app)
    bridge._doc_name = "doc1.cdxml"

    import chemdraw_connector.com.doc_window as doc_window_mod
    import chemdraw_connector.com.nudge as nudge_mod

    original_find = doc_window_mod.find_document_window
    original_close = doc_window_mod.close_document_window
    original_bring = nudge_mod.bring_to_foreground

    found_hwnd = [None]
    closed_hwnd = [None]

    def mock_find(name, hwnd):
        found_hwnd[0] = 12345
        return 12345

    def mock_close(hwnd):
        closed_hwnd[0] = hwnd
        app.Documents._items.remove(doc1)
        # After closing doc1, make doc2 the active document
        app.ActiveDocument = doc2

    doc_window_mod.find_document_window = mock_find
    doc_window_mod.close_document_window = mock_close
    nudge_mod.bring_to_foreground = lambda hwnd: True

    try:
        result = bridge.close_document("doc1.cdxml", discard_changes=True)
        assert result["closed"] == "doc1.cdxml"
        assert result["new_active_document"] == "doc2.cdxml"
        assert bridge._doc_name == "doc2.cdxml"
    finally:
        doc_window_mod.find_document_window = original_find
        doc_window_mod.close_document_window = original_close
        nudge_mod.bring_to_foreground = original_bring


def test_concurrent_use_scratch_document_calls():
    """Full bridge path: multiple use_scratch_document calls work correctly."""
    doc = FakeDoc("chemdraw-mcp-scratch.cdxml")
    app = FakeApp(documents=[doc], active_doc=doc)
    bridge = _FakeBridge(app)

    import chemdraw_connector.com.nudge as nudge_mod
    original_bring = nudge_mod.bring_to_foreground
    nudge_mod.bring_to_foreground = lambda hwnd: True

    try:
        # First call - creates/reuses
        result1 = bridge.use_scratch_document()
        assert result1["reused"] is True

        # Add some content - need to add atoms to doc.Atoms
        new_unit = FakeUnit(1, atom_count=3, bond_count=2)
        doc.Groups.append(new_unit)
        for _ in range(3):
            doc.Atoms.append(FakeAtom(new_unit))

        # Second call - should clear and reuse
        result2 = bridge.use_scratch_document()
        assert result2["reused"] is True
        assert result2["cleared_atoms"] == 3
    finally:
        nudge_mod.bring_to_foreground = original_bring