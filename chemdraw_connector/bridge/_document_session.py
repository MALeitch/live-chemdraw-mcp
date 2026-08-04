"""Document/session lifecycle: connection status, opening/creating/switching
documents, the shared scratch document, save/undo/redo."""
import os
import time

from .. import snapshots, state
from ..com import doc_window, nudge
from ..domain import canvas
from ..errors import ChemDrawError, InvalidInputError
from ._plumbing import SLOW_TIMEOUT


class _DocumentSession:
    def status(self):
        def go():
            info = self._conn.info()
            app = self._conn.app()
            # Raw ActiveDocument can come back None even with exactly one
            # document open (known-flaky COM property, see _doc()'s own
            # docstring) -- _active_document_name's tracked-name/sole-
            # survivor fallbacks cover that case for the reported name
            # without forcing focus. Resolve `doc` through that SAME name
            # (rather than a bare app.ActiveDocument + single-document-
            # only fallback, this call's own prior behavior) so this
            # structures/captions/boxes block can't disagree with the
            # active_document already reported above, and so a flaky
            # ActiveDocument with 2+ documents open -- the common case
            # here, since use_scratch_document deliberately keeps a second
            # document open -- no longer silently blanks this whole block
            # with no error (confirmed live gap, fixed 2026-07-24).
            active_name = self._active_document_name(app)
            info["active_document"] = active_name
            info["open_documents"] = app.Documents.Count
            doc = app.ActiveDocument
            if doc is None and active_name is not None:
                for i in range(1, app.Documents.Count + 1):
                    cand = app.Documents.Item(i)
                    if cand.name == active_name:
                        doc = cand
                        break
            if doc is not None:
                snap = state.build_snapshot(doc, self._cache_for(doc))
                real, wrapper_map, union_wrapper_map, others = canvas.classify_units(snap)
                boxes = self._graphics_boxes(doc)
                info["structures_on_page"] = len(real)
                info["excluded_units"] = {
                    "caption_wrapper_duplicates": len(wrapper_map),
                    "union_wrapper_duplicates": len(union_wrapper_map),
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
        return self._run(go, op_name="status", op_description="status check")

    def new_document(self):
        def go():
            doc = self._conn.app().Documents.Add()
            doc.Activate()
            nudge.bring_to_foreground(self._conn.hwnd)
            return {"active_document": doc.name}
        return self._run(go, op_name="new_document", op_description="create new document")

    def _find_documents_by_name(self, app, name):
        """Every open document whose `.name` matches, in COM collection
        order -- may be more than one (see _resolve_document)."""
        matches = []
        for i in range(1, app.Documents.Count + 1):
            d = app.Documents.Item(i)
            if d.name == name:
                matches.append(d)
        return matches

    def _resolve_document(self, app, name, full_name=None, index=None):
        """Resolve exactly one open document by name, raising a clear
        error rather than silently guessing when `name` alone doesn't
        resolve to exactly one (issue #33: a normal Save-As-to-a-
        different-folder-then-reopen cycle easily produces 2+ open
        documents sharing one filename -- `.name` is just the basename,
        not the full path, so this is common, not exotic). full_name (an
        absolute path, as returned by chemdraw_list_documents'
        document_details[].full_name) disambiguates when there's more
        than one match.

        index (1-based, matching chemdraw_list_documents' own ordering --
        the same app.Documents.Item(i) COM order every lookup in this file
        already uses) is the LAST-RESORT disambiguator, for the real case
        full_name can't cover: the exact same file opened as 2+ separate
        document windows (confirmed live 2026-08-04 -- Save-As/reopen
        duplicates aren't always DIFFERENT files with the same basename,
        sometimes it's the identical path opened twice, which makes
        full_name identical across every match too)."""
        if index is not None:
            try:
                d = app.Documents.Item(index)
            except Exception:
                raise InvalidInputError(
                    f"No open document at index {index} (1-based). "
                    f"chemdraw_list_documents currently shows "
                    f"{app.Documents.Count} open documents, in that same "
                    "order -- it may have changed since you last called it; "
                    "re-list and retry."
                ) from None
            if d.name != name:
                raise InvalidInputError(
                    f"Document at index {index} is now named {d.name!r}, "
                    f"not {name!r} -- the open-document list has changed "
                    "since you last called chemdraw_list_documents. "
                    "Re-list and retry."
                )
            return d
        matches = self._find_documents_by_name(app, name)
        if not matches:
            raise ChemDrawError(
                f"No open document named {name!r}. Open documents: "
                f"{[app.Documents.Item(i).name for i in range(1, app.Documents.Count + 1)]}"
            )
        if len(matches) == 1:
            return matches[0]
        if full_name:
            target_abspath = os.path.normcase(os.path.abspath(full_name))
            full_matches = []
            for d in matches:
                try:
                    if os.path.normcase(os.path.abspath(d.FullName)) == target_abspath:
                        full_matches.append(d)
                except Exception:
                    continue
            if len(full_matches) == 1:
                return full_matches[0]
            if not full_matches:
                raise InvalidInputError(
                    f"{len(matches)} open documents are named {name!r}, but "
                    f"none has full_name {full_name!r}. Their actual paths: "
                    f"{[getattr(d, 'FullName', None) for d in matches]}"
                )
            # 2+ open documents are the SAME file (identical FullName) --
            # full_name genuinely cannot tell them apart; fall through to
            # the index-based error below instead of guessing.
            matches = full_matches
        raise InvalidInputError(
            f"{len(matches)} open documents are named {name!r} -- ambiguous. "
            "Pass full_name (the absolute path -- see chemdraw_list_documents' "
            "document_details) to narrow it down, and if 2+ of those also "
            "share the identical full_name (the same file open more than "
            "once), pass index instead (1-based, same order as "
            f"chemdraw_list_documents' own list). Their actual paths: "
            f"{[getattr(d, 'FullName', None) for d in matches]}"
        )

    @staticmethod
    def _same_document(a, b):
        """Best-effort identity check between two IChemDrawDocument COM
        references. Prefers absolute FullName -- repeated Item()/
        ActiveDocument calls can return distinct Python wrapper objects
        for the same underlying COM document, so plain `is`/`==` isn't
        reliable -- falling back to `.name` only when FullName is empty/
        unavailable (e.g. an untitled document)."""
        try:
            af, bf = a.FullName, b.FullName
            if af and bf:
                return (os.path.normcase(os.path.abspath(af))
                       == os.path.normcase(os.path.abspath(bf)))
        except Exception:
            pass
        try:
            return a.name == b.name
        except Exception:
            return False

    def close_document(self, name, discard_changes=False, full_name=None, index=None):
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
        suppresses that prompt from appearing in the first place).

        If 2+ open documents share `name` (issue #33 -- a normal Save-As-
        to-a-different-folder-then-reopen cycle produces this easily, since
        `.name` is just the basename), this refuses to guess and raises an
        error listing their actual paths; pass full_name (from
        chemdraw_list_documents' document_details) to pick the right one.
        If even full_name is identical across matches (the same file open
        as 2+ separate windows -- confirmed live 2026-08-04, not just a
        theoretical case), pass index instead (1-based, same order as
        chemdraw_list_documents' own list)."""
        return self._run(lambda: self._close_document_now(name, discard_changes, full_name, index),
                         timeout=SLOW_TIMEOUT, op_name="close_document", op_description=f"close document {name}")

    def _close_document_now(self, name, discard_changes=False, full_name=None, index=None):
        """The actual close logic (worker thread only) -- a plain method,
        not wrapped in self._run itself, so edit_stoichiometry_table can
        call it synchronously from inside its own worker callback to
        auto-close a stale throwaway window without re-entering the
        worker (see that method's own docstring)."""
        app = self._conn.app()
        target = self._resolve_document(app, name, full_name, index)
        if target.Modified and not discard_changes:
            raise InvalidInputError(
                f"{name!r} has unsaved changes. Save it first "
                "(chemdraw_save_document, after chemdraw_set_active_"
                "document if it isn't already active) and retry, or "
                "pass discard_changes=true to close without saving."
            )
        was_active = (app.ActiveDocument is not None
                     and self._same_document(app.ActiveDocument, target))
        if discard_changes and target.Modified:
            # Must happen BEFORE the close -- see doc_window.py's
            # module docstring on why this is what actually suppresses
            # ChemDraw's own "Save changes?" modal.
            target.Modified = False

        # Activate the resolved COM object (unambiguous -- a specific
        # object reference, not a name lookup) and read back WHICH window
        # ChemDraw itself now considers active via WM_MDIGETACTIVE, rather
        # than doc_window.find_document_window's title-text search --
        # title text is ambiguous with duplicate open filenames (issue
        # #33), but live MDI activation state is not. See doc_window.py's
        # module docstring for the live confirmation this relies on.
        target.Activate()
        nudge.bring_to_foreground(self._conn.hwnd)
        hwnd = doc_window.active_mdi_child(self._conn.hwnd)
        if hwnd is None:
            # Fallback only: weaker (title-text, ambiguous with
            # duplicates) but better than failing outright if the
            # MDIClient couldn't be located for some reason.
            hwnd = doc_window.find_document_window(name, self._conn.hwnd)
        if hwnd is None:
            raise ChemDrawError(
                f"Could not find {name!r}'s own window to close it "
                "(IChemDrawDocument.Close() itself is a confirmed "
                "no-op over COM on this ChemDraw version)."
            )
        # Captured BEFORE the close for the poll below. Polling by identity
        # ("is the document we closed gone") doesn't work in general here:
        # checking by `name` alone never turns False if another open
        # document shares it, and checking by FullName has the exact same
        # problem when 2+ documents are the SAME file open twice (issue
        # #33 -- confirmed live, not just theoretical, see index's own
        # docstring on _resolve_document). We already targeted the close
        # at a specific, unambiguous hwnd above (via active_mdi_child) --
        # this poll's real job is just "wait for ChemDraw to finish
        # processing that close", which a simple count check answers
        # without needing identity at all.
        count_before = app.Documents.Count
        doc_window.close_document_window(hwnd)

        for _ in range(30):
            time.sleep(0.1)
            if app.Documents.Count < count_before:
                break
        else:
            raise ChemDrawError(
                f"Sent a close request for {name!r} but Documents.Count "
                "did not drop after waiting 3s -- check the ChemDraw "
                "window by hand; it may be showing an unexpected dialog."
            )

        new_active = None
        if was_active:
            # app.ActiveDocument is the same known-flaky COM property
            # _doc() already works around for every OTHER "current
            # document" need in this codebase -- this call just performed
            # a mutation (the close), so unlike the pure read-only
            # _active_document_name helper, a short retry is warranted
            # here: give ChemDraw's own internal re-activation a moment to
            # catch up with the MDI child window's actual destruction.
            active = None
            for _ in range(5):
                active = app.ActiveDocument
                if active is not None:
                    break
                time.sleep(0.1)
            if active is not None:
                new_active = active.name
            elif app.Documents.Count == 1:
                # Still None after retrying, but exactly one document is
                # left -- make it authoritative (Activate it, don't just
                # assume) rather than reporting null when the answer is
                # actually unambiguous.
                sole = app.Documents.Item(1)
                sole.Activate()
                new_active = sole.name
            # else: 0 or 2+ documents remain with ActiveDocument still
            # unresolvable -- genuinely ambiguous, report null rather than
            # guess.
            if new_active is not None:
                self._doc_name = new_active  # keep _doc()'s own fallback in sync
        return {"closed": name, "remaining_documents": app.Documents.Count,
                "new_active_document": new_active}

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
                    nudge.bring_to_foreground(self._conn.hwnd)
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
            nudge.bring_to_foreground(self._conn.hwnd)
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
        return self._run(go, timeout=SLOW_TIMEOUT, op_name="use_scratch_document", op_description="acquire scratch document")

    def convert_cdx_cdxml(self, input_path, output_path, overwrite=False):
        """Convert a file between .cdx/.cdxml (or any other ChemDraw-
        readable/writable format) by opening it and SaveAs-ing under a
        different extension -- ChemDraw's own SaveAs infers format from the
        extension, same as save_document. Not itself wrapped in self._run --
        composes open_document/save_document/close_document/
        set_active_document, each already its own worker submission (same
        composition-above-go() shape as import_molfile).

        Deliberately does not just call open_document+save_document back to
        back: doing so would leave a stray background document open (like
        every other throwaway document, Document.Close() is a no-op over
        COM) and would silently leave the USER'S previously-active document
        no longer active. Detects whether input_path was already an open
        document before this call (the user's own file, or a leftover from
        an earlier call) -- if so, doesn't close it (not ours to close),
        only restores the previously-active document afterward. If
        input_path was already the active document itself, this behaves
        exactly like save_document(output_path) on it directly (its window
        now points at output_path) -- there's nothing to restore.

        The save is wrapped so a FAILED save (most commonly
        save_document's own overwrite=False guard refusing a pre-existing
        output_path -- the single most likely reason this ever raises)
        still leaves things clean: CONFIRMED LIVE that without this, a
        plain, correctly-refused overwrite=False call permanently
        orphaned the newly-opened background document (Document.Close()
        is a no-op over COM, so there was no way to close it after the
        fact either) AND silently left it as the active document -- so
        every subsequent tool call silently operated on the wrong
        document until the user noticed and manually closed the stray
        window."""
        if not os.path.exists(input_path):
            raise InvalidInputError(
                f"No file found at {input_path!r}. Check the path and "
                "retry -- it must point to an existing ChemDraw-readable "
                "file (.cdx/.cdxml/.mol/...)."
            )
        before = self.list_documents()
        documents_before = set(before["documents"])
        prev_active = before["active"]

        opened = self.open_document(input_path)
        bg_name = opened["active_document"]
        newly_opened = bg_name not in documents_before

        try:
            saved = self.save_document(output_path, overwrite)
        except Exception:
            # save_document raised before any COM mutation happened (the
            # overwrite guard is a pure pre-check ahead of doc.SaveAs) --
            # the active document is therefore still exactly bg_name,
            # unchanged, no need to re-query it the way the success path
            # below does. Clean up and restore with the same discipline as
            # a successful call, then propagate the original failure --
            # this is best-effort recovery around the real error, not a
            # replacement for it.
            if newly_opened:
                try:
                    self.close_document(bg_name, discard_changes=True)
                except Exception:
                    pass  # cleanup failure must never mask the real error
            if prev_active is not None and prev_active != bg_name:
                try:
                    if prev_active in set(self.list_documents()["documents"]):
                        self.set_active_document(prev_active)
                except Exception:
                    pass
            raise

        current_name = self.list_documents()["active"]

        # Document names can legitimately be "" (an untitled document,
        # confirmed live) -- these must be `is not None` checks, not
        # truthiness checks, or a real empty-named document gets silently
        # treated the same as "no document"/"nothing to restore".
        if newly_opened and current_name is not None:
            # Our own throwaway -- clean it up now that it's been written
            # out, same "don't leave stray windows behind" discipline as
            # use_scratch_document/import_molfile.
            self.close_document(current_name, discard_changes=True)

        restored = None
        if prev_active is not None and prev_active not in (bg_name, current_name):
            if prev_active in set(self.list_documents()["documents"]):
                self.set_active_document(prev_active)
                restored = prev_active

        return {
            "input_path": input_path,
            "output_path": saved["path"],
            "bytes": os.path.getsize(saved["path"]),
            "reused_existing_document": not newly_opened,
            "restored_active_document": restored,
        }

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
            app = self._conn.app()
            
            # Check if this path is already open in ChemDraw
            target_abspath = os.path.normcase(os.path.abspath(path))
            reused_open_document = False
            existing_doc = None
            for i in range(1, app.Documents.Count + 1):
                cand = app.Documents.Item(i)
                try:
                    cand_path = os.path.normcase(os.path.abspath(cand.FullName))
                    if cand_path == target_abspath:
                        existing_doc = cand
                        reused_open_document = True
                        break
                except Exception:
                    continue
            
            if existing_doc is not None:
                # Document is already open — re-activate it instead of re-opening
                existing_doc.Activate()
                nudge.bring_to_foreground(self._conn.hwnd)
                
                # Compare on-disk mtime with a rough "loaded-at" proxy
                # (we don't have a direct loaded-at timestamp, but we can
                # compare file mtime to current time as a signal)
                try:
                    disk_mtime = os.path.getmtime(path)
                    # We can't know exactly when it was loaded, but returning
                    # the disk mtime lets the caller decide if a reload is needed
                    # by comparing with their own save time
                    return {
                        "active_document": existing_doc.name,
                        "path": path,
                        "reused_open_document": True,
                        "disk_mtime": disk_mtime,
                    }
                except Exception:
                    return {
                        "active_document": existing_doc.name,
                        "path": path,
                        "reused_open_document": True,
                    }
            
            count_before = app.Documents.Count
            doc = app.Documents.Open(path)
            if doc is None:
                # Documents.Open() can return None even when the open
                # actually landed (the same class of flaky document-
                # returning COM property this file already distrusts
                # everywhere else -- see _doc()'s own docstring on
                # ActiveDocument, and set_active_document/
                # _close_document_now re-resolving by scanning
                # app.Documents rather than trusting a raw handle). This
                # was the one document-returning path that didn't, and
                # crashed with a bare AttributeError on doc.Activate().
                doc = self._resolve_opened_document(app, path, count_before)
            doc.Activate()
            nudge.bring_to_foreground(self._conn.hwnd)
            return {"active_document": doc.name, "path": path, "reused_open_document": False}
        return self._run(go, timeout=SLOW_TIMEOUT, op_name="open_document", op_description=f"open {path}")

    @staticmethod
    def _resolve_opened_document(app, path, count_before):
        """Re-resolve Documents.Open(path)'s real result when the COM call
        itself returned None. Scans app.Documents for a FullName match
        first (most reliable), then falls back to the newly-appended last
        item if Documents.Count increased (Open() appends to the
        collection), and only raises if neither signal confirms the open
        actually happened -- never lets a bare None reach .Activate()."""
        target = os.path.normcase(os.path.abspath(path))
        for i in range(1, app.Documents.Count + 1):
            cand = app.Documents.Item(i)
            try:
                same = os.path.normcase(os.path.abspath(cand.FullName)) == target
            except Exception:
                same = False
            if same:
                return cand
        if app.Documents.Count > count_before:
            return app.Documents.Item(app.Documents.Count)
        raise ChemDrawError(
            f"ChemDraw did not open {path!r} — Documents.Open() returned "
            "nothing and no matching document appeared in Documents "
            f"afterward (open_documents stayed at {app.Documents.Count}). "
            "Check the ChemDraw window for an error dialog."
        )

    def save_document(self, path=None, overwrite=False):
        def go():
            doc = self._doc()
            if path:
                self._guard_save_path(doc, path, overwrite)
                doc.SaveAs(path)
            else:
                doc.Save()
            return {"saved": True, "path": path or doc.FullName}
        return self._run(go, timeout=SLOW_TIMEOUT, op_name="save_document", op_description=f"save document")

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
        return self._run(lambda: (self._doc().Undo(), {"ok": True})[1], op_name="undo", op_description="undo")

    def redo(self):
        return self._run(lambda: (self._doc().Redo(), {"ok": True})[1], op_name="redo", op_description="redo")

    def list_documents(self):
        def go():
            app = self._conn.app()
            details = []
            name_counts = {}
            for i in range(1, app.Documents.Count + 1):
                d = app.Documents.Item(i)
                try:
                    full_name = d.FullName
                except Exception:
                    full_name = None
                details.append({"name": d.name, "full_name": full_name})
                name_counts[d.name] = name_counts.get(d.name, 0) + 1
            # See _active_document_name's own docstring: app.ActiveDocument
            # can come back None even with exactly one document open (known-
            # flaky COM property); its sole-survivor fallback covers that
            # case here without forcing window focus on a plain list query.
            return {
                "documents": [d["name"] for d in details],
                "document_details": details,
                # Names sharing 2+ open documents (issue #33) -- a caller
                # must pass full_name to chemdraw_close_document/
                # chemdraw_set_active_document for any name listed here,
                # since `name` alone is ambiguous.
                "duplicate_names": sorted(n for n, c in name_counts.items() if c > 1),
                "active": self._active_document_name(app),
            }
        return self._run(go, op_name="list_documents", op_description="list open documents")

    def set_active_document(self, name, full_name=None, index=None):
        def go():
            app = self._conn.app()
            target = self._resolve_document(app, name, full_name, index)
            target.Activate()
            nudge.bring_to_foreground(self._conn.hwnd)
            return {"active_document": target.name}
        return self._run(go, op_name="set_active_document", op_description=f"set active document {name}")
