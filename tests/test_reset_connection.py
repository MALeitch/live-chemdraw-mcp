"""bridge.reset_connection -- issue #30. Pure, no real COM/process calls;
Connection/ComWorker construction and win32process/psutil are monkeypatched.

The load-bearing property: once a call is genuinely stuck, the worker's one
OS thread can never return to its queue (see com/worker.py's docstring), so
nothing about this recovery path can go through the normal self._run/worker
submission machinery -- it has to replace self._conn/self._worker directly.
"""
import pytest

from chemdraw_connector.bridge import _plumbing as pl
from chemdraw_connector.errors import ChemDrawError, InvalidInputError


class FakeWorker:
    def __init__(self, busy=False, op=None, unblock_hook=None):
        self._busy = busy
        self._op = op
        self.unblock_hook = unblock_hook

    def is_busy(self):
        return self._busy

    def get_current_operation(self):
        return self._op


class FakeConn:
    """Stands in for com.connection.Connection. info_raises makes the
    post-reset reconnect attempt fail, simulating "ChemDraw itself is
    still wedged, not just the old channel"."""

    def __init__(self, hwnd=None, info_raises=False):
        self.hwnd = hwnd
        self.launched = False
        self.info_raises = info_raises
        self.info_calls = 0

    def info(self):
        self.info_calls += 1
        if self.info_raises:
            raise ChemDrawError("still not responding")
        return {"prog_id": "ChemDraw_x64.Application", "executable": "fake.exe"}


class FakePlumbing(pl._Plumbing):
    def __init__(self, worker, conn, doc_name="page.cdxml"):
        self._worker = worker
        self._conn = conn
        self._doc_name = doc_name

    def _run(self, fn, timeout=None, op_name=None, op_description=None):
        # Mirrors the tests/test_scratch_doc_lifecycle.py convention: run
        # inline, no real worker submission needed for these tests.
        return fn()


def _patch_fresh_objects(monkeypatch, new_conn=None, new_worker_busy=False):
    """reset_connection builds fresh Connection()/ComWorker() instances
    directly (not injectable via a constructor arg -- the whole point is
    it can't depend on any existing, possibly-wedged state). Patch the
    classes at their point of use in _plumbing.py."""
    conn_to_return = new_conn or FakeConn()
    monkeypatch.setattr(pl, "Connection", lambda: conn_to_return)
    monkeypatch.setattr(
        pl, "ComWorker",
        lambda unblock_hook=None: FakeWorker(busy=new_worker_busy, unblock_hook=unblock_hook))
    return conn_to_return


def test_reset_connection_requires_confirm():
    bridge = FakePlumbing(FakeWorker(busy=True), FakeConn())
    with pytest.raises(InvalidInputError, match="confirm"):
        bridge.reset_connection(kill_process=False, confirm=False)


def test_reset_connection_refuses_when_worker_not_busy():
    bridge = FakePlumbing(FakeWorker(busy=False), FakeConn())
    with pytest.raises(InvalidInputError, match="not currently busy"):
        bridge.reset_connection(kill_process=False, confirm=True)


def test_reset_connection_swaps_worker_and_connection(monkeypatch):
    old_worker = FakeWorker(busy=True, op={"name": "transform", "elapsed": 200.0})
    old_conn = FakeConn(hwnd=123)
    bridge = FakePlumbing(old_worker, old_conn)

    new_conn = _patch_fresh_objects(monkeypatch)
    result = bridge.reset_connection(kill_process=False, confirm=True)

    assert bridge._worker is not old_worker
    assert bridge._conn is new_conn
    assert bridge._doc_name is None
    assert result["reset"] is True
    assert result["killed_process"] is None
    assert result["abandoned_operation"] == {"name": "transform", "elapsed": 200.0}
    assert result["reconnected"] is True
    assert result["reconnect_error"] is None


def test_reset_connection_reports_failed_reconnect_without_raising(monkeypatch):
    old_worker = FakeWorker(busy=True)
    bridge = FakePlumbing(old_worker, FakeConn(hwnd=123))

    _patch_fresh_objects(monkeypatch, new_conn=FakeConn(info_raises=True))
    result = bridge.reset_connection(kill_process=False, confirm=True)

    # The worker/connection were still swapped -- the OLD wedged one is
    # abandoned regardless of whether the reattach itself succeeded.
    assert bridge._worker is not old_worker
    assert result["reset"] is True
    assert result["reconnected"] is False
    assert result["reconnect_error"] is not None


def test_reset_connection_kill_process_kills_by_the_attached_pid(monkeypatch):
    old_worker = FakeWorker(busy=True)
    bridge = FakePlumbing(old_worker, FakeConn(hwnd=999))

    import win32process
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (1, 4242))

    killed = []

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def kill(self):
            killed.append(self.pid)

    import psutil
    monkeypatch.setattr(psutil, "Process", FakeProcess)

    _patch_fresh_objects(monkeypatch)
    result = bridge.reset_connection(kill_process=True, confirm=True)

    assert killed == [4242]
    assert result["killed_process"] == 4242
    assert bridge._worker is not old_worker


def test_reset_connection_kill_process_without_hwnd_raises_and_leaves_worker_untouched():
    old_worker = FakeWorker(busy=True)
    bridge = FakePlumbing(old_worker, FakeConn(hwnd=None))

    with pytest.raises(ChemDrawError, match="process id"):
        bridge.reset_connection(kill_process=True, confirm=True)

    # The wedged worker must survive a failed kill attempt -- callers can
    # retry (e.g. kill by hand, then kill_process=False to just reattach).
    assert bridge._worker is old_worker


def test_reset_connection_kill_process_failure_leaves_worker_untouched(monkeypatch):
    old_worker = FakeWorker(busy=True)
    bridge = FakePlumbing(old_worker, FakeConn(hwnd=999))

    import win32process
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (1, 4242))

    class ExplodingProcess:
        def __init__(self, pid):
            pass

        def kill(self):
            raise OSError("Access is denied")

    import psutil
    monkeypatch.setattr(psutil, "Process", ExplodingProcess)

    with pytest.raises(ChemDrawError, match="Could not kill"):
        bridge.reset_connection(kill_process=True, confirm=True)

    assert bridge._worker is old_worker
