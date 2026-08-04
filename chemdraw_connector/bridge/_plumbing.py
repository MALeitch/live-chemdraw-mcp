"""Foundational plumbing shared by every other bridge mixin: the COM worker
submission wrapper, document resolution, per-document caching, backup
debouncing, and RDKit pre-validation. Every other mixin's methods call
straight into `self._run`/`self._doc`/`self._cache_for`/`self._maybe_snapshot`
via the composed ChemDrawBridge instance."""
import os

import pythoncom
import pywintypes

from .. import snapshots, state, targets
from ..com import nudge
from ..com import types as t
from ..com.connection import PROG_ID, Connection, is_dead_proxy
from ..com.worker import ComWorker, DEFAULT_TIMEOUT
from ..domain import enumeration, wrapper_detection
from ..errors import ChemDrawError, InvalidInputError, UnexpectedConnectorError

SLOW_TIMEOUT = 45.0  # name<->structure conversions and batch ops can be slow
# expand_labels intentionally makes ONE COM call covering every matching
# label at once (verified safe — unlike contraction, ExpandLabelsToStructure
# doesn't merge separate structures, see bridge docstring on expand_labels).
# A single call doing genuinely more total ChemDraw-side work needs more
# runway than SLOW_TIMEOUT before the wedge detector should worry.
BULK_TIMEOUT = 180.0


def _com_text(data):
    if isinstance(data, memoryview):
        return data.tobytes().decode("utf-8", "replace")
    return "" if data is None else str(data)


def _com_bytes(data):
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return _com_text(data).encode("utf-8")


class _Plumbing:
    def __init__(self):
        self._conn = Connection()
        self._worker = ComWorker(unblock_hook=self._nudge_chemdraw)
        self._last_snapshot = None  # for diff_since_last_check
        self._doc_name = None  # tracked working document (see _doc)
        self._caches = {}  # doc.name -> targets.py unit/atom/bond cache
        self._last_backup = {}  # doc.name -> {"sig": ..., "path": ...}

    def _cache_for(self, doc):
        """Per-document cache passed to targets.iter_units/resolve/
        find_by_id/unit_atoms_bonds so repeated calls skip full-document
        COM rescans when nothing has changed (see targets.doc_signature).
        Keyed by doc.name, the same document-identity key _doc() itself
        tracks, so switching the active document can never serve one
        document's cached units for another."""
        return self._caches.setdefault(doc.name, {})

    def _maybe_snapshot(self, doc):
        """Backup-on-mutation, debounced: reuse the last backup's path when
        doc_signature(doc) hasn't changed since it was taken (same
        signature check as the unit/atom/bond cache), instead of
        re-exporting + rewriting a fresh CDXML file on every mutating call
        in a rapid chain. When a fresh backup IS needed, the COM export
        happens here (must run on the worker thread) but the disk write is
        handed off (see snapshots.write_backup_file(wait=False)) so a slow
        disk never blocks the worker thread. Worker thread only."""
        sig = targets.doc_signature(doc)
        last = self._last_backup.get(doc.name)
        if last is not None and last["sig"] == sig:
            return last["path"]
        text = snapshots.export_cdxml_text(doc)
        if text is None:
            return None
        path = snapshots.write_backup_file(doc.name, text, wait=False)
        self._last_backup[doc.name] = {"sig": sig, "path": path}
        return path

    def _nudge_chemdraw(self):
        """Break ChemDraw out of an interactive block (in-canvas text edit).
        Runs off the COM worker thread via pure Win32 — see com/nudge.py."""
        return bool(nudge.nudge_escape(self._conn.hwnd))

    def _run(self, fn, timeout=DEFAULT_TIMEOUT, op_name=None, op_description=None):
        def wrapped():
            try:
                return fn()
            except pywintypes.com_error as exc:
                if is_dead_proxy(exc):
                    # ChemDraw died mid-call. Reconnect for future calls, but
                    # report honestly — a silent retry against a fresh instance
                    # would masquerade as "object not found."
                    try:
                        self._conn.app()
                    except Exception:
                        pass
                    raise ChemDrawError(
                        "ChemDraw stopped responding during this operation (it "
                        "may have crashed) and a fresh instance was reconnected. "
                        "Check the ChemDraw window — unsaved work from before "
                        "the crash may be recoverable from the automatic "
                        f"backups in {snapshots.BACKUP_DIR}"
                    ) from exc
                raise ChemDrawError(self._explain(exc)) from exc
            except ChemDrawError:
                # Already the correct, intentional client-facing type (e.g.
                # raised by validation upstream) — pass through unchanged.
                raise
            except Exception as exc:
                # Anything else (a plain ValueError from bad input, an
                # unexpected AttributeError from a real bug, an RDKit
                # exception, ...) would otherwise reach the MCP client as a
                # raw, unfiltered Python exception string. Wrap it in the
                # connector's usual readable-error vocabulary — chaining
                # (`from exc`) keeps the original traceback intact for
                # server-side debugging even though the client only sees the
                # friendly message.
                raise UnexpectedConnectorError(exc) from exc
        return self._worker.submit(wrapped, timeout=timeout, op_name=op_name, op_description=op_description)

    @staticmethod
    def _explain(exc):
        parts = [str(p) for p in (exc.excepinfo or []) if isinstance(p, str) and p]
        detail = "; ".join(parts) or exc.strerror or str(exc)
        return f"ChemDraw rejected the operation: {detail}"

    def fast_status(self):
        """Connector-side health state that NEVER dispatches to COM.

        Answers: is the worker busy, with which operation, started how long
        ago; plus process liveness, PID, and launched_by_connector.

        The whole point is that this answers WHILE a COM call is in flight —
        that is the moment you need to tell "busy since 12 minutes ago" from
        "dead". So every field here must come from in-process state or plain
        Win32, never from the Application proxy.

        In particular this must NOT call `self._conn.info()`: that reads
        `app.FullName`, which is a COM property get, which blocks on a busy
        STA exactly like everything else. An earlier version did, which made
        this call useless in precisely the situation it exists for.

        PID comes from the cached `hwnd` via GetWindowThreadProcessId — a
        Win32 call, unaffected by a blocked COM queue, which is why
        Connection caches that handle in the first place. That also makes the
        PID unambiguous when more than one ChemDraw is running: it is the
        window of the instance we are actually attached to, not merely the
        first process whose image name matches. That distinction matters —
        a second instance may hold unsaved user work, and this is the field
        a caller consults before deciding whether killing is safe.

        Every probe is individually guarded: a status call that raises during
        a wedge is no more useful than one that blocks.
        """
        worker = {"busy": None, "current_operation": None, "error": None}
        try:
            worker["busy"] = self._worker.is_busy()
            worker["current_operation"] = self._worker.get_current_operation()
        except Exception as exc:                      # pragma: no cover
            worker["error"] = f"{type(exc).__name__}: {exc}"

        hwnd = getattr(self._conn, "hwnd", None)
        pid, process_alive, exe_path = None, None, None
        try:
            if hwnd:
                import win32process
                _tid, pid = win32process.GetWindowThreadProcessId(int(hwnd))
        except Exception:
            pid = None
        if pid:
            try:
                import psutil
                process_alive = psutil.pid_exists(pid)
                if process_alive:
                    try:
                        exe_path = psutil.Process(pid).exe()
                    except Exception:
                        exe_path = None
            except Exception:
                process_alive = None

        return {
            "worker": worker,
            "chem_draw": {
                "process_alive": process_alive,
                "pid": pid,
                "hwnd": hwnd,
                # False here means a ChemDraw we attached to rather than
                # started: it may hold documents we never opened.
                "launched_by_connector": getattr(self._conn, "launched", False),
                "executable": exe_path,
            },
            "connection": {
                "prog_id": PROG_ID,
            },
            "tracked_document": self._doc_name,
        }

    def reset_connection(self, kill_process=False, confirm=False):
        """Recover from a genuinely stuck COM call (issue #30) WITHOUT
        restarting this connector's own process.

        Background: a native COM call blocked inside ChemDraw cannot be
        interrupted from Python — there is no safe way to abort a thread
        mid-syscall. Once the worker's one dedicated STA thread is stuck
        inside such a call, it can NEVER return to its queue to pick up
        new work (see com/worker.py's own docstring), so every future
        call fails fast forever with "a previous call is still waiting on
        ChemDraw" — confirmed live 2026-08-04 that this genuinely never
        self-heals. Until now the only recovery was restarting the whole
        MCP server process.

        This works instead by simply ABANDONING the stuck worker/
        connection (its thread is daemon=True, so it never blocks process
        exit; if the stuck call ever does complete in the background,
        its result is just never read — the same "may still land later"
        caveat ChemDrawBlockedError already documents) and building a
        FRESH ComWorker + Connection for every call from here on.

        kill_process=False (the default): only the connector's OWN link
        is reset — it attempts to reattach to the SAME still-running
        ChemDraw process (GetActiveObject, same as any normal reconnect).
        If the stuck call was some client-side channel oddity rather than
        ChemDraw's own UI thread genuinely being busy, this recovers with
        ZERO disruption: no window closed, nothing unsaved lost. If
        ChemDraw's own internal thread really is the thing that's stuck,
        this reattach attempt will itself likely time out too (reported
        under `reconnect_error`, not raised) — that's the signal to retry
        with kill_process=True.

        kill_process=True: also kills the ChemDraw.exe process (by the
        EXACT pid this connector is attached to — the same pid
        chemdraw_status(fast=true) reports, avoiding the "which ChemDraw"
        ambiguity hazard when 2+ instances are running) before building
        the fresh connection, which then launches a new instance.
        DESTRUCTIVE: closes every open ChemDraw window, discarding any
        unsaved changes not already on disk. Recent automatic backups
        live under `snapshots.BACKUP_DIR`.

        Requires confirm=True (this is a destructive, not-fully-
        reversible action — the abandoned operation's true outcome
        becomes unknowable, and kill_process additionally destroys
        unsaved state)."""
        if not confirm:
            raise InvalidInputError(
                "reset_connection abandons whatever operation is "
                "currently in flight (its effects, if any, may still "
                "land on the canvas later with nobody watching), and "
                "with kill_process=true also closes every open ChemDraw "
                "window, losing any unsaved changes not already on disk "
                f"(recent automatic backups: {snapshots.BACKUP_DIR}). "
                "Pass confirm=true to proceed."
            )
        was_busy = self._worker.is_busy()
        if not was_busy:
            raise InvalidInputError(
                "The worker is not currently busy — there is nothing to "
                "reset. Use chemdraw_status(fast=true) first to confirm "
                "a call is genuinely stuck (worker.busy=true with "
                "current_operation.elapsed climbing across repeated "
                "checks) before calling this."
            )
        stuck_operation = self._worker.get_current_operation()

        killed_pid = None
        if kill_process:
            hwnd = getattr(self._conn, "hwnd", None)
            pid = None
            try:
                if hwnd:
                    import win32process
                    _tid, pid = win32process.GetWindowThreadProcessId(int(hwnd))
            except Exception:
                pid = None
            if not pid:
                raise ChemDrawError(
                    "Could not determine the ChemDraw process id to kill "
                    "(no cached window handle on this connection) — the "
                    "stuck worker was NOT reset. Kill it by hand via Task "
                    "Manager, then retry with kill_process=false to just "
                    "reattach."
                )
            try:
                import psutil
                psutil.Process(pid).kill()
                killed_pid = pid
            except Exception as exc:
                raise ChemDrawError(
                    f"Could not kill ChemDraw process {pid}: {exc}. The "
                    "stuck worker was NOT reset — retry, or kill it by "
                    "hand via Task Manager and call this again with "
                    "kill_process=false to just reattach."
                ) from exc

        # Abandon the wedged worker/connection outright — see docstring.
        self._conn = Connection()
        self._worker = ComWorker(unblock_hook=self._nudge_chemdraw)
        self._doc_name = None  # the new connection has no tracked document yet

        reconnect_error = None
        info = None
        try:
            info = self._run(
                lambda: self._conn.info(), timeout=DEFAULT_TIMEOUT,
                op_name="reset_connection", op_description="reconnect after reset")
        except Exception as exc:
            reconnect_error = str(exc)

        return {
            "reset": True,
            "killed_process": killed_pid,
            "abandoned_operation": stuck_operation,
            "reconnected": info is not None,
            "connection": info,
            "reconnect_error": reconnect_error,
        }

    def _doc(self):
        """The working document. Worker thread only.

        Quirk (probed live): ActiveDocument intermittently returns None over
        COM — even mid-session for documents that were being used moments
        before. So: trust ActiveDocument when it answers (it tracks the user's
        real focus), but remember the working document's name and fall back to
        re-resolving that name when ActiveDocument goes blank, instead of
        grabbing an arbitrary document or spawning new ones.
        """
        app = self._conn.app()
        doc = app.ActiveDocument
        if doc is not None:
            self._doc_name = doc.name
            return doc
        if self._doc_name:
            for i in range(1, app.Documents.Count + 1):
                cand = app.Documents.Item(i)
                if cand.name == self._doc_name:
                    return cand
            self._doc_name = None  # tracked document was closed
        if app.Documents.Count > 0:
            doc = app.Documents.Item(app.Documents.Count)
        else:
            doc = app.Documents.Add()
        doc.Activate()
        nudge.bring_to_foreground(self._conn.hwnd)
        self._doc_name = doc.name
        return doc

    def _active_document_name(self, app):
        """Best-effort active-document name for a pure status/read query —
        no retry, no .Activate() side effect (a status call must never
        move window focus). app.ActiveDocument is the same known-flaky COM
        property _doc() works around for mutating calls (see its own
        docstring). Three read-only tiers: (1) trust ActiveDocument when
        it answers; (2) fall back to the tracked working document name
        (self._doc_name, kept in sync by every mutating call via _doc())
        if it's still open — this covers the common case of 2+ documents
        open (this connector's own scratch document deliberately kept
        open alongside the user's real one, by design — see
        use_scratch_document) where ActiveDocument comes back flaky-None
        and there's no sole survivor to fall back to either; confirmed
        live, this used to silently blank chemdraw_status's entire
        structures/captions/boxes block with no error in exactly that
        case; (3) the sole-survivor fallback for the remaining case where
        exactly one document is open but nothing has been tracked yet."""
        active = app.ActiveDocument
        if active is not None:
            return active.name
        if self._doc_name:
            for i in range(1, app.Documents.Count + 1):
                cand = app.Documents.Item(i)
                if cand.name == self._doc_name:
                    return cand.name
        if app.Documents.Count == 1:
            return app.Documents.Item(1).name
        return None

    @staticmethod
    def _insert_raw(objs, mime, payload):
        """Worker thread only. `payload` is UTF-8-encoded to bytes before
        the COM property-put, never handed over as a Python str.

        CONFIRMED LIVE (data-loss bug, not cosmetic): a CDXML text payload
        built by domain/reagent_text.build_text_cdxml containing a
        character outside cp1252's repertoire (the rightwards arrow
        U+2192, e.g. in a conditions_text of "0 C->rt") came back from a
        round trip through this call as a literal "?" actually stored in
        the live document -- confirmed via chemdraw_describe_canvas after
        the insert, not just a rendering artifact. A character cp1252 DOES
        cover (the degree sign, U+00B0, in the same string) survived
        intact, which is what isolates the mechanism: passing `payload` as
        a plain Python str lets pywin32 marshal it as a BSTR (UTF-16), and
        ChemDraw's own Data-property setter for text mime types narrows
        that incoming BSTR to the system ANSI codepage before its CDXML
        parser ever runs -- any character outside that codepage is lost
        (replaced with '?') right there, upstream of and regardless of
        whatever encoding the CDXML prolog itself declares. This is the
        write-side mirror of the ALREADY-DIAGNOSED, DIFFERENT read-side bug
        in domain/mojibake.py (there, ChemDraw's internal UTF-8 bytes get
        WIDENED via the system codepage on the way OUT over COM, producing
        reversible double-encoding mojibake, not data loss); the fix here
        is the opposite direction and not reversible after the fact, so it
        has to be avoided rather than repaired.

        Passing `payload` as bytes instead makes pywin32 marshal it as a
        byte-array VARIANT rather than a BSTR, which is the same
        byte-oriented path already proven lossless for binary Data in this
        codebase (see _com_bytes/_com_text above handling a memoryview
        GetData result for binary mime types like image/png) -- it hands
        ChemDraw the actual UTF-8 bytes to parse per the CDXML prolog's own
        encoding="UTF-8" declaration (see build_text_cdxml) instead of a
        wide string ChemDraw narrows through cp1252 first. Every payload
        through this call is plain text today (SMILES/molfile/name/CDXML
        strings -- see _insert_structure_units and _reaction.py's
        _make_annotation_caption, the only two callers), so the str check
        below is a forward-compatibility guard, not evidence a caller ever
        needs to pass bytes itself.

        NOT independently confirmed against a live ChemDraw session (the
        live document has real unsaved user work right now) -- confirmed
        via code-level reasoning plus the existing pure-Python test suite
        (tests/test_reagent_text.py's payload-shape assertions) rather than
        an actual round trip. Manual live verification still needed: build
        a scheme with conditions_text containing U+2192, read it back via
        chemdraw_describe_canvas, and confirm the arrow (not "?") comes
        back."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        raw = objs._oleobj_
        dispid = raw.GetIDsOfNames(0, "Data")
        raw.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, mime, payload)

    @staticmethod
    def _set_position(obj, x, y):
        """Move a positional object (caption, arrow, atom) to x, y in points."""
        try:
            pos = obj.Position
            pos.X = x
            pos.Y = y
            obj.Position = pos
            return True
        except Exception:
            return False

    @staticmethod
    def _guard_write_path(path, overwrite, create_parent=False):
        """Validate a generic file-write target (export_image, CSV table
        exports) before writing — same "an LLM-constructed path shouldn't
        silently destroy data" motivation as
        _DocumentSession._guard_save_path, without that method's "same
        file as the currently open document" exemption: there's no
        equivalent legitimate case for an image/CSV export, every target
        path here is a distinct output file, not the document itself.

        create_parent=False (the default, every existing caller):
        refuses with an actionable error naming the missing directory,
        same as every sibling write guard.

        create_parent=True: auto-creates the missing parent directory
        instead of refusing. ONLY for a caller writing to ITS OWN
        well-known default location that this connector owns and
        controls (e.g. export_canvas_table's default path, same
        discipline as snapshots.write_backup_file's own
        os.makedirs(exist_ok=True)) — never for an explicit/custom path,
        which is exactly the "LLM-constructed path" case this guard
        exists to protect. _write_csv_rows used to
        os.makedirs(exist_ok=True) unconditionally for EVERY path,
        including an explicit custom one, silently creating a directory
        tree instead of the actionable error this default gives."""
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            if create_parent:
                os.makedirs(parent, exist_ok=True)
            else:
                raise InvalidInputError(
                    f"Cannot write to {path!r}: the containing directory "
                    f"{parent!r} does not exist. Create it first or choose "
                    "a different path."
                )
        if os.path.exists(path) and not overwrite:
            raise InvalidInputError(
                f"{path!r} already exists. Refusing to silently overwrite "
                "it — pass overwrite=True if that's really intended, or "
                "choose a different path."
            )

    def _validate_input(self, representation, fmt):
        """RDKit pre-validation for formats it can parse; Kekulized output
        (explicit double bonds, no aromatic-circle rendering in ChemDraw —
        see enumeration.validate_smiles). Formats RDKit can't judge (name,
        cdxml, helm...) pass through unchanged."""
        if fmt == "smiles":
            return enumeration.validate_smiles(representation)
        if fmt in ("molfile", "molfile-v3000"):
            return enumeration.validate_molblock(representation)
        return representation

    def _insert_structure_units(self, doc, representation, fmt, include_wrappers=False):
        """Insert and return the new units (worker thread). Verifies atoms
        actually appeared — an unknown/silently-ignored payload is an error,
        not a success.

        include_wrappers=False (the default, and every existing caller's
        behavior, unchanged): returns just the list of real units, wrapper
        groups silently dropped (see _split_wrapper_groups).
        include_wrappers=True: returns (units, wrapper_entries) instead --
        only chemdraw_insert_structure itself opts into this, to surface a
        wrapper's own id/children (see bridge/_structure_io.py) without
        touching the other 3 call sites (_layout.py's build_scope_table,
        _reaction.py's reactant/product insertion x2), which only ever
        need the real units and would have to be updated to unpack a
        2-tuple for no benefit to them."""
        mime = t.mime_for(fmt)
        atoms_before = doc.Atoms.Count
        groups_before = doc.Groups.Count
        self._insert_raw(doc.Objects, mime, representation)
        if doc.Atoms.Count == atoms_before:
            raise InvalidInputError(
                f"ChemDraw did not insert anything for the given {fmt} input "
                f"({representation[:80]!r}). Check the input is valid."
            )
        new_units = [
            doc.Groups.Item(i)
            for i in range(groups_before + 1, doc.Groups.Count + 1)
        ]
        kept, wrappers = self._split_wrapper_groups(new_units)
        # Every OTHER document mutation either changes doc_signature in a
        # way the NEXT iter_units/status/describe_canvas call notices on
        # its own, or explicitly invalidates first (see resolve_atom/
        # resolve_bond's own _invalidate_cache precedent). This insert
        # path relied ENTIRELY on that indirect signal — the new Groups/
        # Atoms/Bonds counts differing from whatever was cached before —
        # with no invalidation of its own. Confirmed live: a query call
        # immediately after insert_structure could still see the stale
        # pre-insert unit list for one call. Force it explicitly instead
        # of trusting the next caller's signature check to catch it.
        targets._invalidate_cache(self._cache_for(doc))
        return (kept, wrappers) if include_wrappers else kept

    @staticmethod
    def _split_wrapper_groups(units):
        """A single insertion payload with disconnected fragments (a salt,
        e.g. "[Na+].[OH-]") makes ChemDraw register one extra wrapper Group
        -- bounds and formula equal to the UNION of its own child fragments
        -- in doc.Groups alongside those children, which also each appear
        independently. Confirmed live: chemdraw_insert_structure on
        "[Na+].[OH-]" returned 3 groups (HNaO wrapping Na+ and HO-), not the
        2 real ions; treating the wrapper as a positionable unit alongside
        its own children double-moved/double-positioned the same
        underlying atoms, which is what made reaction-scheme salt
        fragments overlap their own wrapper.

        Splits out any unit whose bounding box strictly contains one or
        more other units' in this same batch -- a wrapper's bounds always
        strictly contain its children's combined bounds (larger area, not
        just touching), while two genuinely independent fragments never
        fully contain one another (disconnected structures never overlap
        by construction). Only compares units from the SAME insertion
        call, never against pre-existing document content, so this can't
        misfire on an unrelated nearby structure.

        Returns (kept_units, wrapper_entries): kept_units excludes every
        detected wrapper (same "don't double-position it" behavior as
        before -- callers that don't care about wrappers can just use
        this list unchanged); wrapper_entries is a list of
        {"unit": <wrapper Group>, "children": [<child Groups>]}, one per
        detected wrapper, for a caller (chemdraw_insert_structure) that
        wants to surface the wrapper's own id/children rather than
        silently discard it. Falls back to (units, []) if bounds can't be
        read for some reason, rather than risking dropping a real
        structure on a guess."""
        boxes = []
        for u in units:
            try:
                boxes.append((u.Left, u.Top, u.Right, u.Bottom))
            except Exception:
                return units, []

        contain_map = wrapper_detection.find_containing_wrappers(boxes, tol=0.5)
        wrappers = [
            {"unit": units[i], "children": [units[j] for j in kids]}
            for i, kids in contain_map.items()
        ]
        kept = [u for i, u in enumerate(units) if i not in contain_map]
        return (kept if kept else units), wrappers

    def _describe_units(self, doc, units):
        w, h = float(doc.Width or 540), float(doc.Height or 720)
        return [state.describe_unit(u, w, h) for u in units]
