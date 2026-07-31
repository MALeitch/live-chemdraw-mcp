"""Single dedicated STA thread that owns every COM call.

COM automation objects are apartment-threaded: the thread that creates them
must make every call on them. This worker serializes all ChemDraw access
through one queue. A timed-out call marks the worker wedged (the thread is
stuck inside a blocking native call — likely a ChemDraw modal dialog) and
subsequent submissions fail fast until the stuck call finally returns.
"""
import queue
import threading
from concurrent.futures import Future

from ..errors import ChemDrawBlockedError

DEFAULT_TIMEOUT = 15.0
NUDGE_GRACE = 6.0  # extra wait after nudging ChemDraw out of a text edit


class ComWorker:
    def __init__(self, unblock_hook=None):
        self._queue = queue.Queue()
        self._wedged = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        # Called (off the worker thread) when a call times out, to try to
        # break ChemDraw out of an interactive block. Returns truthy if it
        # did something worth waiting a moment longer for.
        self._unblock_hook = unblock_hook

    def _ensure_thread(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="chemdraw-com", daemon=True
                )
                self._thread.start()

    def _run(self):
        import pythoncom

        pythoncom.CoInitialize()
        while True:
            fn, future = self._queue.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 — must reach the caller
                future.set_exception(exc)
            finally:
                # Completing anything proves the thread is alive again.
                self._wedged.clear()

    def submit(self, fn, timeout=DEFAULT_TIMEOUT):
        """Run fn() on the COM thread and return its result.

        On timeout, tries to nudge ChemDraw out of an interactive block (e.g.
        an in-canvas text edit) and waits a short grace period for the call to
        finish. Only if that fails does it mark the worker wedged and raise.
        """
        if self._wedged.is_set():
            # A prior call is genuinely stuck. Try one more nudge in case the
            # user is mid-edit, then fail fast rather than queue behind it.
            self._try_unblock()
            raise ChemDrawBlockedError(
                "(a previous call is still waiting on ChemDraw -- if it "
                "eventually completes, its effects will still land on the "
                "canvas even though it was reported as blocked; check "
                "chemdraw_get_document_state before assuming nothing "
                "happened.)"
            )
        self._ensure_thread()
        future = Future()
        self._queue.put((fn, future))
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            pass

        # The operation had its full timeout. Before giving up, nudge ChemDraw
        # (pure Win32, works even though the COM channel is blocked) and give
        # the now-unblocked call a moment to complete.
        if self._try_unblock():
            try:
                result = future.result(timeout=NUDGE_GRACE)
                self._wedged.clear()
                return result
            except TimeoutError:
                pass

        self._wedged.set()
        # FIXED (DEBUG_REPORT.md M-1, 2026-07-31): fn is still running on
        # the worker thread at this point -- it was never cancelled, Future
        # can't interrupt a callable already in flight -- so this raise
        # tells the caller "blocked/failed" while the mutation itself may
        # still land moments later. Say so explicitly rather than implying
        # nothing happened.
        raise ChemDrawBlockedError(
            "(this call's own operation may still be running in the "
            "background and could still complete or mutate the document "
            "after this error is returned -- it was not and cannot be "
            "cancelled. Re-check chemdraw_get_document_state before "
            "retrying, to avoid applying the same change twice.)"
        ) from None

    def _try_unblock(self):
        if self._unblock_hook is None:
            return False
        try:
            return bool(self._unblock_hook())
        except Exception:
            return False
