"""convert_cdx_cdxml's own orchestration logic -- pure, no COM/win32.

Regression test. CONFIRMED LIVE that a failed save_document() call
inside convert_cdx_cdxml (most commonly the overwrite=False guard
correctly refusing a pre-existing
output_path -- the single most likely reason this ever raises) used to
leave the newly-opened background document permanently orphaned
(Document.Close() is a no-op over COM, so there was no way to close it
after the fact either) AND silently left it as the active document, so
every subsequent tool call operated on the wrong document.

This tests ONLY the try/except cleanup added around the save -- not
open_document/save_document/close_document themselves, each of which is
already covered elsewhere (test_document_session_open.py etc.). The five
methods convert_cdx_cdxml composes are stubbed with plain Python state
transitions instead of real COM/win32, so this is specifically an
orchestration-logic test.
"""
import os

import pytest

from chemdraw_connector.bridge import _document_session as ds
from chemdraw_connector.errors import InvalidInputError


class _StubBridge(ds._DocumentSession):
    def __init__(self, documents, active):
        self._documents = list(documents)
        self._active = active
        self.closed = []      # names passed to close_document, in order
        self.activated = []   # names passed to set_active_document, in order
        self.save_raises = None

    def list_documents(self):
        return {"documents": list(self._documents), "active": self._active}

    def open_document(self, path):
        name = os.path.basename(path)
        if name not in self._documents:
            self._documents.append(name)
        self._active = name
        return {"active_document": name, "path": path}

    def save_document(self, path=None, overwrite=False):
        if self.save_raises is not None:
            raise self.save_raises
        # Simulate SaveAs renaming the active document's window -- same as
        # the real bridge, where doc.name reflects the new path afterward.
        new_name = os.path.basename(path)
        if self._active in self._documents:
            self._documents.remove(self._active)
        self._documents.append(new_name)
        self._active = new_name
        return {"saved": True, "path": path}

    def close_document(self, name, discard_changes=False):
        self.closed.append(name)
        if name in self._documents:
            self._documents.remove(name)
        if self._active == name:
            self._active = None
        return {"closed": name, "remaining_documents": len(self._documents),
                "new_active_document": self._active}

    def set_active_document(self, name):
        self.activated.append(name)
        self._active = name
        return {"active_document": name}


def test_failed_save_closes_newly_opened_background_doc_and_restores_active(tmp_path):
    input_path = tmp_path / "input.cdxml"
    input_path.write_text("<CDXML/>")

    bridge = _StubBridge(documents=["scratch.cdxml"], active="scratch.cdxml")
    bridge.save_raises = InvalidInputError("already exists")

    with pytest.raises(InvalidInputError):
        bridge.convert_cdx_cdxml(str(input_path), str(tmp_path / "out.cdxml"))

    # The background document convert_cdx_cdxml itself opened must be
    # closed, not left orphaned -- this is the core H-2 fix.
    assert bridge.closed == ["input.cdxml"]
    # The document that was active before this call must be restored.
    assert bridge.activated == ["scratch.cdxml"]
    assert bridge._active == "scratch.cdxml"
    assert bridge._documents == ["scratch.cdxml"]


def test_failed_save_on_already_open_input_does_not_close_it(tmp_path):
    # input_path was ALREADY an open document before this call (the
    # user's own file, or a leftover from an earlier call) -- "not ours
    # to close" must still hold on the failure path, same as success.
    input_path = tmp_path / "input.cdxml"
    input_path.write_text("<CDXML/>")

    bridge = _StubBridge(documents=["other.cdxml", "input.cdxml"],
                         active="other.cdxml")
    bridge.save_raises = InvalidInputError("already exists")

    with pytest.raises(InvalidInputError):
        bridge.convert_cdx_cdxml(str(input_path), str(tmp_path / "out.cdxml"))

    assert bridge.closed == []  # not ours to close
    assert bridge.activated == ["other.cdxml"]  # still restored
    assert bridge._active == "other.cdxml"
    assert set(bridge._documents) == {"other.cdxml", "input.cdxml"}


def test_failed_save_original_exception_propagates_unchanged(tmp_path):
    input_path = tmp_path / "input.cdxml"
    input_path.write_text("<CDXML/>")

    bridge = _StubBridge(documents=[], active=None)
    original = InvalidInputError("out.cdxml already exists and is a "
                                  "different file than the one currently open")
    bridge.save_raises = original

    with pytest.raises(InvalidInputError) as exc_info:
        bridge.convert_cdx_cdxml(str(input_path), str(tmp_path / "out.cdxml"))
    assert exc_info.value is original


def test_successful_convert_still_closes_and_restores(tmp_path):
    input_path = tmp_path / "input.cdxml"
    input_path.write_text("<CDXML/>")
    output_path = tmp_path / "out.cdxml"

    bridge = _StubBridge(documents=["scratch.cdxml"], active="scratch.cdxml")
    # save_document's stub writes the real file so os.path.getsize succeeds.
    real_save = bridge.save_document
    def _save_and_write(path=None, overwrite=False):
        result = real_save(path, overwrite)
        with open(path, "w") as fh:
            fh.write("<CDXML/>")
        return result
    bridge.save_document = _save_and_write

    result = bridge.convert_cdx_cdxml(str(input_path), str(output_path))

    assert result["reused_existing_document"] is False
    assert result["restored_active_document"] == "scratch.cdxml"
    assert bridge.closed == ["out.cdxml"]  # our own throwaway, renamed by SaveAs
    assert bridge._active == "scratch.cdxml"
    assert bridge._documents == ["scratch.cdxml"]
