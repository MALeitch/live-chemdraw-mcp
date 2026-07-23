"""Document/session lifecycle: connection status, opening/creating/switching
documents, the shared scratch document, save/undo/redo."""
import os
import time

from .. import snapshots, state
from ..com import doc_window
from ..domain import canvas
from ..errors import ChemDrawError, InvalidInputError
from ._plumbing import SLOW_TIMEOUT


class _DocumentSession:
    def status(self):
        def go():
            info = self._conn.info()
            app = self._conn.app()
            doc = app.ActiveDocument
            info["active_document"] = doc.name if doc is not None else None
            info["open_documents"] = app.Documents.Count
            if doc is not None:
                snap = state.build_snapshot(doc, self._cache_for(doc))
                real, wrapper_map, others = canvas.classify_units(snap)
                boxes = self._graphics_boxes(doc)
                info["structures_on_page"] = len(real)
                info["excluded_units"] = {
                    "caption_wrapper_duplicates": len(wrapper_map),
                    "decoration_groups": len(others),
                }
                info["captions_on_page"] = doc.Captions.Count
                info["boxes_on_page"] = len(boxes)
                per_box = {}
                unboxed = 0
                for s in real:
                    idx = (canvas.containing_box(s["bounds"], boxes)
                           if s.get("bounds") else None)
                    if idx is None:
                        unboxed += 1
                    else:
                        per_box[idx] = per_box.get(idx, 0) + 1
                info["structures_per_box"] = [
                    {"box_index": b["index"],
                     "structure_count": per_box.get(b["index"], 0)}
                    for b in boxes
                ]
                if unboxed:
                    info["structures_per_box"].append(
                        {"box_index": None, "structure_count": unboxed})
                info["chemical_warnings"] = doc.NumChemicalWarnings
            return info
        return self._run(go)

    def new_document(self):
        def go():
            doc = self._conn.app().Documents.Add()
            doc.Activate()
            return {"active_document": doc.name}
        return self._run(go)

    def close_document(self, name, discard_changes=False):
        """Close one open document by name -- IChemDrawDocument.Close() is
        a confirmed no-op over COM, so this goes around it via a Win32
        WM_CLOSE posted straight to that document's own MDI child window
        (see chemdraw_connector/com/doc_window.py for the full story on how
        that was found and confirmed safe).

        A document with unsaved changes (`.Modified`) is refused unless
        discard_changes=True, exactly like chemdraw_save_document's own
        overwrite guard -- closing throws away anything not already saved
        to disk, and there is no way to ask ChemDraw's own "Save changes?"
        prompt to answer for us (see doc_window.py: this only works at all
        because Modified is cleared BEFORE the close, which is what
        suppresses that prompt from appearing in the first place)."""
        def go():
            app = self._conn.app()
            target = None
            for i in range(1, app.Documents.Count + 1):
                d = app.Documents.Item(i)
                if d.name == name:
                    target = d
                    break
            if target is None:
                raise ChemDrawError(
                    f"No open document named {name!r}. Open documents: "
                    f"{[app.Documents.Item(i).name for i in range(1, app.Documents.Count + 1)]}"
                )
            if target.Modified and not discard_changes:
                raise InvalidInputError(
                    f"{name!r} has unsaved changes. Save it first "
                    "(chemdraw_save_document, after chemdraw_set_active_"
                    "document if it isn't already active) and retry, or "
                    "pass discard_changes=true to close without saving."
                )
            was_active = (app.ActiveDocument is not None
                         and app.ActiveDocument.name == name)
            if discard_changes and target.Modified:
                # Must happen BEFORE the close -- see doc_window.py's
                # module docstring on why this is what actually suppresses
                # ChemDraw's own "Save changes?" modal.
                target.Modified = False

            hwnd = doc_window.find_document_window(name, self._conn.hwnd)
            if hwnd is None:
                raise ChemDrawError(
                    f"Could not find {name!r}'s own window to close it "
                    "(IChemDrawDocument.Close() itself is a confirmed "
                    "no-op over COM on this ChemDraw version)."
                )
            doc_window.close_document_window(hwnd)

            for _ in range(30):
                time.sleep(0.1)
                still_open = any(
                    app.Documents.Item(i).name == name
                    for i in range(1, app.Documents.Count + 1))
                if not still_open:
                    break
            else:
                raise ChemDrawError(
                    f"Sent a close request for {name!r} but it still shows "
                    "up in Documents after waiting 3s -- check the ChemDraw "
                    "window by hand; it may be showing an unexpected dialog."
                )

            new_active = None
            if was_active:
                active = app.ActiveDocument
                new_active = active.name if active is not None else None
            return {"closed": name, "remaining_documents": app.Documents.Count,
                    "new_active_document": new_active}
        return self._run(go, timeout=SLOW_TIMEOUT)

    # One reusable scratch document. Document.Close() is a no-op over COM
    # (probed live on ChemDraw 26 — every variant), so every throwaway
    # document becomes a permanent window until the user closes it by hand.
    # All temporary/test work therefore shares this single document, cleared
    # on each acquisition.
    SCRATCH_DOC_NAME = "chemdraw-mcp-scratch.cdxml"

    def use_scratch_document(self):
        def go():
            app = self._conn.app()
            for i in range(1, app.Documents.Count + 1):
                doc = app.Documents.Item(i)
                if doc.name == self.SCRATCH_DOC_NAME:
                    doc.Activate()
                    cleared = doc.Atoms.Count
                    doc.Objects.Clear()
                    # doc.Objects.Clear() (above) does NOT clear doc.Graphics —
                    # confirmed by test_bridge.py's own comment (~line 372):
                    # "the shared scratch document can carry stray doc.Graphics
                    # debris across sessions". Box-frame/decoration graphics
                    # would otherwise silently accumulate across reuses.
                    # doc.Graphics.Clear() is attempted by analogy: Clear() is
                    # the established removal pattern elsewhere in this
                    # codebase on IChemDrawObjects-family collections
                    # (doc.Objects.Clear() just above, and
                    # targets.unit_objects(u).Clear() in bridge.remove()), so
                    # it's plausible Graphics supports it the same way — but
                    # this specific collection/method pair has NOT been
                    # live-verified. Guarded so a failure here (unsupported
                    # method, or any other COM error) can never break scratch
                    # acquisition, which is this call's actual job. NEEDS LIVE
                    # CONFIRMATION: check that box-frame graphics actually
                    # disappear from the scratch doc after a second
                    # acquisition, and that this call doesn't error/crash.
                    graphics_before = doc.Graphics.Count
                    graphics_cleared = 0
                    try:
                        doc.Graphics.Clear()
                        graphics_cleared = graphics_before
                    except Exception:
                        pass
                    self._doc_name = doc.name
                    return {"active_document": doc.name, "reused": True,
                            "cleared_atoms": cleared,
                            "cleared_graphics": graphics_cleared}
            doc = app.Documents.Add()
            doc.Activate()
            entry = {"reused": False, "cleared_atoms": 0}
            try:
                path = os.path.join(os.path.dirname(snapshots.BACKUP_DIR),
                                    self.SCRATCH_DOC_NAME)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                doc.SaveAs(path)
            except Exception as exc:
                # Still usable, just not findable by name next time.
                entry["note"] = (f"scratch document could not be saved under "
                                 f"its stable name: {exc}")
            self._doc_name = doc.name
            entry["active_document"] = doc.name
            return entry
        return self._run(go, timeout=SLOW_TIMEOUT)

    def open_document(self, path):
        def go():
            # An LLM client constructs this path, not a careful human typing
            # it — validate up front so a hallucinated/misconstrued path
            # fails with a clear, actionable message instead of a cryptic
            # COM-layer error from Documents.Open.
            if not os.path.exists(path):
                raise InvalidInputError(
                    f"No file found at {path!r}. Check the path and retry — "
                    "it must point to an existing ChemDraw-readable file "
                    "(.cdx/.cdxml/.mol/...)."
                )
            if not os.path.isfile(path):
                raise InvalidInputError(
                    f"{path!r} exists but is not a file (e.g. a directory). "
                    "Pass the path to the document itself."
                )
            doc = self._conn.app().Documents.Open(path)
            doc.Activate()
            return {"active_document": doc.name, "path": path}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def save_document(self, path=None, overwrite=False):
        def go():
            doc = self._doc()
            if path:
                self._guard_save_path(doc, path, overwrite)
                doc.SaveAs(path)
            else:
                doc.Save()
            return {"saved": True, "path": path or doc.FullName}
        return self._run(go, timeout=SLOW_TIMEOUT)

    @staticmethod
    def _guard_save_path(doc, path, overwrite):
        """Validate a Save As target before handing it to COM. Two checks,
        both aimed at "an LLM-constructed path shouldn't silently destroy
        data", not at sandboxing (the user already has full filesystem
        access — path traversal etc. is out of scope here):

        1. Parent directory must exist — SaveAs into a missing directory
           fails deep inside COM with an opaque error; catch it here with a
           message that actually names the problem.
        2. If `path` already exists on disk and is NOT the same file the
           active document was already opened from, require an explicit
           overwrite=True instead of silently clobbering it. Re-saving the
           document you already have open (path == doc.FullName) is the
           common "save my current document" case and is exempt — this only
           gates SaveAs onto a *different*, pre-existing file, which is the
           scenario a hallucinated path could hit by accident.

        `overwrite` is exposed by the chemdraw_save_document MCP tool
        (tools/structure.py) with the same default of False, so this gate is
        bypassable but only deliberately, one call at a time.
        """
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            raise InvalidInputError(
                f"Cannot save to {path!r}: the containing directory "
                f"{parent!r} does not exist. Create it first or choose a "
                "different path."
            )
        if not os.path.exists(path) or overwrite:
            return
        try:
            current = doc.FullName
        except Exception:
            current = None
        same_file = False
        if current:
            try:
                same_file = (os.path.normcase(os.path.abspath(path))
                             == os.path.normcase(os.path.abspath(current)))
            except Exception:
                same_file = False
        if not same_file:
            raise InvalidInputError(
                f"{path!r} already exists and is a different file than the "
                "one currently open. Refusing to silently overwrite it — "
                "pass overwrite=True if that's really intended, or choose a "
                "different path."
            )

    def undo(self):
        return self._run(lambda: (self._doc().Undo(), {"ok": True})[1])

    def redo(self):
        return self._run(lambda: (self._doc().Redo(), {"ok": True})[1])

    def list_documents(self):
        def go():
            app = self._conn.app()
            active = app.ActiveDocument
            return {
                "documents": [
                    app.Documents.Item(i).name
                    for i in range(1, app.Documents.Count + 1)
                ],
                "active": active.name if active is not None else None,
            }
        return self._run(go)

    def set_active_document(self, name):
        def go():
            app = self._conn.app()
            for i in range(1, app.Documents.Count + 1):
                doc = app.Documents.Item(i)
                if doc.name == name:
                    doc.Activate()
                    return {"active_document": doc.name}
            raise ChemDrawError(
                f"No open document named {name!r}. Open documents: "
                f"{[app.Documents.Item(i).name for i in range(1, app.Documents.Count + 1)]}"
            )
        return self._run(go)
