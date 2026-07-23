"""Win32 workaround for closing a ChemDraw document -- IChemDrawDocument.Close()
is a confirmed no-op over COM on this ChemDraw version (probed live: called
with zero args and with SaveOption 0/1/2, Documents.Count never changed; see
bridge/_document_session.py and tools/structure.py for where that limitation
used to be treated as final).

CONFIRMED LIVE INSTEAD: ChemDraw is a classic MDI app, not one top-level
window per document -- there is exactly ONE visible "CSWFrame" window (the
same class nudge.py already enumerates), and every open document is its own
"CSWDocument" child window beneath it (doc.Documents.Count matched the
CSWDocument child count exactly in testing: 14 documents, 14 child windows).
The child's title is the document's `.name`, with a trailing " *" appended
while `.Modified` is True.

Posting WM_CLOSE to that one CSWDocument child closes only that document --
confirmed live: Documents.Count dropped by exactly one, the other documents
and the main frame were untouched, and no dialog appeared PROVIDED
`.Modified` was set to False first (a real, settable COM property, unlike
the read-only-in-practice `.Saved`, which raised AttributeError when
assigned in testing). Skipping that step risks WM_CLOSE popping ChemDraw's
own "Save changes?" modal, which would then block this same Win32 message
channel the same way an in-progress caption edit does (see nudge.py) --
so a caller here MUST have already confirmed the document should lose its
unsaved changes before this module ever sets Modified=False.
"""
import win32api
import win32con
import win32gui

from .nudge import find_chemdraw_frames

_DOCUMENT_CLASS = "CSWDocument"


def find_document_window(doc_name, preferred_frame_hwnd=None):
    """The CSWDocument child window for one open document, by its `.name`,
    or None if no match is found (e.g. the title's trailing " *" wasn't
    stripped correctly, or the window closed already). Searches the
    preferred frame first (the automated instance's own, if still valid)
    like nudge.nudge_escape does, falling back to every CSWFrame found."""
    frames = []
    if preferred_frame_hwnd and win32gui.IsWindow(preferred_frame_hwnd):
        frames = [preferred_frame_hwnd]
    if not frames:
        frames = find_chemdraw_frames()

    for frame in frames:
        kids = []
        try:
            win32gui.EnumChildWindows(frame, lambda h, _: kids.append(h), None)
        except Exception:
            continue
        for h in kids:
            try:
                if win32gui.GetClassName(h) != _DOCUMENT_CLASS:
                    continue
                if win32gui.GetWindowText(h).rstrip(" *") == doc_name:
                    return h
            except Exception:
                continue
    return None


def close_document_window(hwnd):
    """Post WM_CLOSE to one CSWDocument child. Posted (not sent), same
    fire-and-forget style as nudge._post_escape -- the caller polls
    Documents.Count afterward rather than waiting on this call."""
    win32api.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
