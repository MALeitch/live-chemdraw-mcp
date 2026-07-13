"""Attach/launch/reconnect logic. Every function here must run on the COM
worker thread (they touch COM objects)."""
import pythoncom
import pywintypes
import win32com.client
import win32com.client.gencache

from ..errors import ChemDrawNotAvailableError

PROG_ID = "ChemDraw_x64.Application"

# HRESULTs raised when the automated instance has gone away.
_DEAD_PROXY_HRESULTS = {
    -2147023174,  # 0x800706BA RPC server unavailable
    -2147417848,  # 0x80010108 object disconnected from clients
    -2147023170,  # 0x800706BE remote procedure call failed
    -2147418111,  # 0x80010001 call rejected by callee
}


def is_dead_proxy(exc):
    if isinstance(exc, pywintypes.com_error):
        return exc.hresult in _DEAD_PROXY_HRESULTS
    return False


class Connection:
    """Holds the Application proxy; reconnects if ChemDraw was closed."""

    def __init__(self):
        self._app = None
        self.launched = False  # True if we started ChemDraw ourselves
        # Top-level frame window handle, cached on each successful connect so
        # the Win32 unblock nudge can target this instance even while COM is
        # blocked. Plain int — safe to read from another thread.
        self.hwnd = None

    def _cache_hwnd(self, app):
        try:
            self.hwnd = int(app.MainWindow)
        except Exception:
            pass

    def app(self):
        if self._app is not None:
            try:
                _ = self._app.Visible  # cheap liveness probe
                return self._app
            except pywintypes.com_error as exc:
                if not is_dead_proxy(exc):
                    raise
                self._app = None
        try:
            raw = win32com.client.GetActiveObject(PROG_ID)
            self.launched = False
        except pythoncom.com_error:
            try:
                raw = win32com.client.Dispatch(PROG_ID)
                self.launched = True
            except pythoncom.com_error as exc:
                raise ChemDrawNotAvailableError(
                    f"Could not attach to or launch ChemDraw ({PROG_ID}): {exc}"
                ) from exc
        app = win32com.client.gencache.EnsureDispatch(raw)
        app.Visible = True
        self._app = app
        self._cache_hwnd(app)
        return app

    def info(self):
        app = self.app()
        typelib = type(app).__module__  # e.g. ...gen_py.<guid>x0x22x0.IChemDrawApplication
        return {
            "prog_id": PROG_ID,
            "executable": app.FullName,
            "type_library": getattr(app, "name", "") or "",
            "wrapper_module": typelib,
            "launched_by_connector": self.launched,
        }
