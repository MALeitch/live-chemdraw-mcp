"""chemdraw_open_document — Documents.Open() returning None, pure/no COM.

Confirmed live: Application.Documents.Open(path) can return None even when
the open actually landed (the same class of flaky document-returning COM
property this codebase already distrusts everywhere else -- see _doc()'s
own docstring on ActiveDocument). open_document() used to trust that
return value directly and crash with AttributeError on doc.Activate().
These tests verify the re-resolution fallback added in
_document_session._resolve_opened_document instead.
"""
import os
import time

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
        self.open_calls = 0          # proves whether disk was re-read at all

    @property
    def Count(self):
        return len(self._docs)

    def Item(self, i):
        return self._docs[i - 1]

    def Open(self, path):
        self.open_calls += 1
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

    def _run(self, fn, timeout=None, op_name=None, op_description=None):
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


# --- ROADMAP #25: already-open documents return the stale in-memory copy ----
#
# Re-opening a path ChemDraw already holds re-activates the existing window
# instead of re-reading the file. That behaviour is defensible -- it protects
# a user's unsaved edits -- but silent it is a trap: a caller that writes a
# file and immediately opens it to inspect the result gets the PREVIOUS
# version with no indication anything is wrong.
#
# Real failure this caused: wrote a CDXML, rendered it, found a bug, fixed the
# generator, rewrote the SAME path, re-opened, re-rendered -- and got a
# pixel-identical image, nearly concluding the fix had not worked. Only the
# implausibility of two byte-identical renders caught it.
#
# So the contract under test is the SIGNAL, not a forced reload.

def test_fresh_open_reports_it_was_not_reused(tmp_path):
    path = tmp_path / "fresh.cdxml"
    path.write_text("<CDXML/>")
    doc = FakeDoc("fresh.cdxml", str(path))
    docs = FakeDocuments(docs=[])
    docs.open_return = doc
    bridge = _FakeSessionBridge(FakeApp(docs))

    result = bridge.open_document(str(path))
    assert result["reused_open_document"] is False
    assert docs.open_calls == 1


def test_already_open_path_is_flagged_as_reused(tmp_path):
    path = tmp_path / "open.cdxml"
    path.write_text("<CDXML/>")
    already = FakeDoc("open.cdxml", str(path))
    docs = FakeDocuments(docs=[already])
    bridge = _FakeSessionBridge(FakeApp(docs))

    result = bridge.open_document(str(path))
    assert result["reused_open_document"] is True, (
        "without this flag the caller cannot tell it is looking at a stale "
        "in-memory copy rather than what is on disk"
    )
    assert already.activated


def test_reusing_does_not_re_read_the_file(tmp_path):
    """The whole hazard: Open() is never called, so disk changes are unseen."""
    path = tmp_path / "open.cdxml"
    path.write_text("<CDXML/>")
    docs = FakeDocuments(docs=[FakeDoc("open.cdxml", str(path))])
    bridge = _FakeSessionBridge(FakeApp(docs))

    bridge.open_document(str(path))
    assert docs.open_calls == 0


def test_reused_open_reports_disk_mtime_so_staleness_is_detectable(tmp_path):
    """mtime is what lets a caller compare against its own last write."""
    path = tmp_path / "open.cdxml"
    path.write_text("<CDXML/>")
    docs = FakeDocuments(docs=[FakeDoc("open.cdxml", str(path))])
    bridge = _FakeSessionBridge(FakeApp(docs))

    result = bridge.open_document(str(path))
    assert result["disk_mtime"] == pytest.approx(path.stat().st_mtime, abs=1)


def test_rewriting_the_file_still_reuses_but_mtime_moves(tmp_path):
    """Reproduces the exact trap: same path rewritten, still the stale copy,
    but the reported mtime now differs from what the caller last saw."""
    path = tmp_path / "page.cdxml"
    path.write_text("<CDXML/>")
    docs = FakeDocuments(docs=[FakeDoc("page.cdxml", str(path))])
    bridge = _FakeSessionBridge(FakeApp(docs))

    first = bridge.open_document(str(path))

    time.sleep(0.01)
    path.write_text("<CDXML><!-- regenerated --></CDXML>")
    os.utime(path, (time.time() + 5, time.time() + 5))

    second = bridge.open_document(str(path))
    assert second["reused_open_document"] is True
    assert second["disk_mtime"] > first["disk_mtime"], (
        "the caller's only way to notice the on-disk file moved under the "
        "still-loaded copy"
    )
    assert docs.open_calls == 0


def test_path_matching_tolerates_case_and_separator_differences(tmp_path):
    """Windows paths reach us in whatever form the caller happened to build."""
    path = tmp_path / "Mixed.cdxml"
    path.write_text("<CDXML/>")
    docs = FakeDocuments(docs=[FakeDoc("Mixed.cdxml", str(path))])
    bridge = _FakeSessionBridge(FakeApp(docs))

    odd = str(path).replace("\\", "/").upper()
    result = bridge.open_document(odd if os.path.exists(odd) else str(path))
    assert result["reused_open_document"] is True


def test_a_different_document_is_not_mistaken_for_the_target(tmp_path):
    other = tmp_path / "other.cdxml"
    other.write_text("<CDXML/>")
    target = tmp_path / "target.cdxml"
    target.write_text("<CDXML/>")

    docs = FakeDocuments(docs=[FakeDoc("other.cdxml", str(other))])
    docs.open_return = FakeDoc("target.cdxml", str(target))
    bridge = _FakeSessionBridge(FakeApp(docs))

    result = bridge.open_document(str(target))
    assert result["reused_open_document"] is False
    assert docs.open_calls == 1
