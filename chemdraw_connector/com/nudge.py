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
