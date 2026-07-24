"""Win32 escape hatch for when ChemDraw is wedged in an interactive state.

An in-progress on-canvas text edit (or a caption the user started typing and
walked away from) puts ChemDraw's UI thread in a modal message loop that
blocks the COM automation channel. COM can't break out of that — but the Win32
window message queue is a separate channel that still accepts input. Posting an
Escape to the focused edit control ends the edit, the modal loop exits, and the
stuck COM call completes on its own.

Everything here is pure Win32 (no COM), so it is safe to call from another
thread while the COM worker thread is blocked.
"""
import ctypes
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32process

_user32 = ctypes.windll.user32
_FRAME_CLASS = "CSWFrame"  # ChemDraw's top-level frame window class


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def find_chemdraw_frames():
    """All visible ChemDraw top-level frame windows, by class name."""
    out = []

    def cb(hwnd, _):
        try:
            if win32gui.GetClassName(hwnd) == _FRAME_CLASS:
                out.append(hwnd)
        except Exception:
            pass

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return out


def _focused_control(frame_hwnd):
    """The control with keyboard focus/caret in the frame's UI thread — i.e.
    the text-edit box when a caption is being edited. Falls back to the frame."""
    try:
        tid, _ = win32process.GetWindowThreadProcessId(frame_hwnd)
        gti = _GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if _user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            return gti.hwndFocus or gti.hwndCaret or frame_hwnd
    except Exception:
        pass
    return frame_hwnd


def _post_escape(hwnd):
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)


def nudge_escape(preferred_hwnd=None):
    """Post Escape to ChemDraw to end an in-progress text edit.

    Targets the automated instance's frame (preferred_hwnd) when it's still a
    valid window; otherwise every ChemDraw frame found. Escape on a frame that
    isn't editing is a harmless no-op. Returns the list of hwnds nudged.
    """
    frames = []
    if preferred_hwnd and win32gui.IsWindow(preferred_hwnd):
        frames = [preferred_hwnd]
    if not frames:
        frames = find_chemdraw_frames()

    nudged = []
    for frame in frames:
        try:
            _post_escape(_focused_control(frame))
            nudged.append(frame)
        except Exception:
            continue
    return nudged


def bring_to_foreground(hwnd):
    """Force one ChemDraw top-level frame window to the OS foreground and
    un-minimize it.

    CONFIRMED LIVE (2026-07-24): `doc.Activate()` over COM only updates
    ChemDraw's own internal notion of which MDI child document is active --
    it does not touch the CSWFrame's actual window state. Called from this
    connector's background process (not the OS foreground app), that COM
    call left a document's content fully correct and queryable while the
    window itself never visibly appeared to the user, and separately
    correlates with the `ActiveDocument` COM property intermittently
    returning None even for a document just activated (see
    bridge/_plumbing.py's `_doc()` docstring for that quirk, previously
    worked around there by remembering the doc name rather than fixing the
    underlying focus problem) -- consistent with ChemDraw's own idea of
    "active document" being tied to real window focus, which a bare
    doc.Activate() from a background process never establishes.

    SetForegroundWindow alone is normally denied by Windows' foreground-lock
    when the caller isn't already the OS-foreground process (the standard
    protection against background apps stealing focus) -- so the fallback
    below attaches this process's input queue to the current foreground
    window's owning thread first, which is the standard, well-documented way
    to get Windows to actually honor the request; it detaches again
    immediately after regardless of the outcome.

    Best-effort and silent: every failure is swallowed. By the time this
    runs, the caller's real COM work (open/activate/save) has already
    succeeded -- losing window focus here is a UX regression, not a
    correctness one, so it must never surface as an error.
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            pass
        cur_thread = win32api.GetCurrentThreadId()
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
        attached = False
        try:
            if fg_thread and fg_thread != cur_thread:
                attached = win32process.AttachThreadInput(cur_thread, fg_thread, True)
            win32gui.SetForegroundWindow(hwnd)
            return True
        finally:
            if attached:
                win32process.AttachThreadInput(cur_thread, fg_thread, False)
    except Exception:
        return False
