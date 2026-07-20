"""_Plumbing._run's exception translation — pure, no live ChemDraw.

_run is the single chokepoint every mutating bridge call goes through. It
must present a consistent, client-readable ChemDrawError to the MCP client
no matter what kind of exception the wrapped callable raises:

- pywintypes.com_error -> ChemDrawError (existing behavior, exercised
  elsewhere via the higher-level bridge tests / test_worker.py's
  ChemDrawBlockedError path).
- an already-intentional ChemDrawError (or subclass) -> passed through
  unchanged, not double-wrapped.
- anything else unexpected (ValueError, AttributeError, ...) -> wrapped in
  UnexpectedConnectorError instead of leaking a raw Python exception string
  to the client, while chaining (`from exc`) keeps the original traceback
  available server-side.
"""
import pywintypes
import pytest

from chemdraw_connector.bridge._plumbing import _Plumbing
from chemdraw_connector.errors import (
    ChemDrawError,
    NothingSelectedError,
    UnexpectedConnectorError,
)


@pytest.fixture
def plumbing():
    return _Plumbing()


def test_com_error_is_translated_to_chemdraw_error(plumbing):
    def boom():
        raise pywintypes.com_error(-2147352567, "generic failure", None, None)

    with pytest.raises(ChemDrawError) as excinfo:
        plumbing._run(boom, timeout=5)
    assert not isinstance(excinfo.value, UnexpectedConnectorError)


def test_existing_chemdraw_error_passes_through_unwrapped(plumbing):
    def boom():
        raise NothingSelectedError()

    with pytest.raises(NothingSelectedError):
        plumbing._run(boom, timeout=5)


def test_unexpected_exception_is_wrapped_not_leaked(plumbing):
    def boom():
        raise ValueError("bad input somewhere upstream")

    with pytest.raises(UnexpectedConnectorError) as excinfo:
        plumbing._run(boom, timeout=5)

    message = str(excinfo.value)
    # Original exception's type and message must not be discarded.
    assert "ValueError" in message
    assert "bad input somewhere upstream" in message
    # Traceback chaining preserved for server-side debugging.
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_unexpected_exception_is_a_chemdraw_error(plumbing):
    # So MCP-layer callers that catch ChemDrawError broadly still catch this.
    def boom():
        raise AttributeError("unexpected attribute access")

    with pytest.raises(ChemDrawError):
        plumbing._run(boom, timeout=5)
