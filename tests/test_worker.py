"""Worker timeout/nudge/recover flow — pure, no COM."""
import threading
import time

import pytest

from chemdraw_connector.com.worker import ComWorker
from chemdraw_connector.errors import ChemDrawBlockedError


def test_normal_call_returns():
    w = ComWorker()
    assert w.submit(lambda: 42, timeout=2) == 42


def test_nudge_recovers_a_stalled_call():
    # Simulate a call blocked inside COM until an external nudge releases it —
    # exactly the in-canvas-text-edit scenario: the worker thread is stuck, and
    # a Win32 Escape (here, the hook setting an Event) frees it.
    released = threading.Event()
    hook_calls = []

    def blocked():
        released.wait(timeout=5)
        return "done"

    def hook():
        hook_calls.append(1)
        released.set()  # the "Escape" that ends the edit
        return True

    w = ComWorker(unblock_hook=hook)
    # Short timeout so we exercise the timeout->nudge->grace path quickly.
    assert w.submit(blocked, timeout=0.3) == "done"
    assert hook_calls, "nudge hook should have been invoked on timeout"
    assert not w._wedged.is_set(), "worker should not stay wedged after recovery"


def test_unrecoverable_block_raises_and_wedges():
    stuck = threading.Event()

    def blocked():
        stuck.wait(timeout=5)  # never released during the test

    w = ComWorker(unblock_hook=lambda: False)  # nudge can't help
    with pytest.raises(ChemDrawBlockedError):
        w.submit(blocked, timeout=0.2)
    assert w._wedged.is_set()
    # A subsequent call fails fast instead of queueing behind the stuck one.
    with pytest.raises(ChemDrawBlockedError):
        w.submit(lambda: 1, timeout=0.2)
    stuck.set()


def test_hook_exception_is_swallowed():
    def blocked():
        time.sleep(5)

    def bad_hook():
        raise RuntimeError("win32 boom")

    w = ComWorker(unblock_hook=bad_hook)
    with pytest.raises(ChemDrawBlockedError):
        w.submit(blocked, timeout=0.2)
