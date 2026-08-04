"""chemdraw_status(fast=True) — ROADMAP #26. Pure, no COM.

The entire value of this call is that it answers WHILE a COM operation is
in flight. A health check that queues behind the thing you are diagnosing
cannot tell you "busy since 12 minutes ago" versus "dead" — which was the
actual gap: during two real wedges the only way to distinguish them was
shelling out to `Get-Process ChemDraw` and watching cumulative CPU.

So the load-bearing assertion here is not the shape of the dict. It is that
nothing on this path dispatches to COM at all.
"""
import time

import pytest

from chemdraw_connector.bridge import _plumbing as pl


class ExplodingApp:
    """Any attribute touch is a COM call. During a wedge these block."""

    def __getattr__(self, name):
        raise AssertionError(
            f"fast_status touched COM (app.{name}) — it must answer from "
            f"in-process state only, or it blocks exactly when it is needed"
        )


class FakeConn:
    def __init__(self, launched=False, executable=r"Z:\nonexistent\ChemDraw.exe"):
        self.launched = launched
        self.executable = executable
        self.app_calls = 0
        self.hwnd = 0

    def app(self):
        self.app_calls += 1
        return ExplodingApp()

    def info(self):
        # Mirrors the real connection.info(), which reads app.FullName etc.
        app = self.app()
        return {
            "prog_id": "ChemDraw_x64.Application",
            "executable": app.FullName,
            "type_library": "",
            "launched_by_connector": self.launched,
        }


class FakeWorker:
    def __init__(self, busy=False, op=None):
        self._busy = busy
        self._op = op

    def is_busy(self):
        return self._busy

    def get_current_operation(self):
        return self._op


class FakePlumbing(pl._Plumbing):
    def __init__(self, worker, conn, doc_name="page.cdxml"):
        self._worker = worker
        self._conn = conn
        self._doc_name = doc_name


def _bridge(busy=False, op=None, launched=False):
    return FakePlumbing(FakeWorker(busy, op), FakeConn(launched=launched))


# --- the load-bearing one ---------------------------------------------------
def test_fast_status_never_dispatches_to_com():
    """If this fails, fast_status blocks during a wedge — i.e. it is useless."""
    conn = FakeConn()
    bridge = FakePlumbing(FakeWorker(busy=True, op={"name": "transform",
                                                    "start_time": time.time(),
                                                    "description": "clean"}),
                          conn)
    bridge.fast_status()
    assert conn.app_calls == 0, (
        "fast_status called conn.app(); connection.info() reads app.FullName, "
        "so this path queues behind any in-flight COM call"
    )


def test_fast_status_answers_while_the_worker_is_busy():
    op = {"name": "transform", "start_time": time.time() - 42.0,
          "description": "clean action=clean"}
    res = _bridge(busy=True, op=op).fast_status()
    assert res["worker"]["busy"] is True
    assert res["worker"]["current_operation"] is not None


def test_fast_status_reports_idle_when_nothing_is_running():
    res = _bridge(busy=False, op=None).fast_status()
    assert res["worker"]["busy"] is False
    assert res["worker"]["current_operation"] is None


def test_fast_status_names_the_in_flight_operation():
    """'busy' alone doesn't tell you whether to wait or intervene."""
    op = {"name": "transform", "start_time": time.time() - 900.0,
          "description": "clean action=clean"}
    res = _bridge(busy=True, op=op).fast_status()
    current = res["worker"]["current_operation"]
    assert current["name"] == "transform"
    assert "clean" in (current.get("description") or "")


def test_fast_status_exposes_launched_by_connector():
    """This is the 'is it safe to kill?' signal. A second ChemDraw instance
    the connector did NOT start may hold unsaved user work."""
    assert _bridge(launched=True).fast_status()["chem_draw"]["launched_by_connector"] is True
    assert _bridge(launched=False).fast_status()["chem_draw"]["launched_by_connector"] is False


def test_fast_status_reports_tracked_document():
    res = _bridge().fast_status()
    assert res["tracked_document"] == "page.cdxml"


def test_fast_status_survives_a_worker_that_cannot_answer():
    """Degrade to a partial answer rather than raising — a status call that
    throws during a wedge is no better than one that blocks."""
    class BrokenWorker:
        def is_busy(self):
            raise RuntimeError("worker lock poisoned")

        def get_current_operation(self):
            raise RuntimeError("worker lock poisoned")

    bridge = FakePlumbing(BrokenWorker(), FakeConn())
    res = bridge.fast_status()
    assert isinstance(res, dict)
