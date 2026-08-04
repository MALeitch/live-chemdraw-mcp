"""Win32 workaround for closing a ChemDraw document -- IChemDrawDocument.Close()
is a confirmed no-op over COM on this ChemDraw version (probed live: called
with zero args and with SaveOption 0/1/2, Documents.Count never changed; see
bridge/_document_session.py and tools/structure.py for where that limitation
used to be treated as final).

CONFIRMED LIVE INSTEAD: ChemDraw is a classic MDI app, not one top-level
window per document -- there is exactly ONE visible "CSWFrame" window (the
same class nudge.py already enumerates), one "MDIClient" child beneath it,
and every open document is its own "CSWDocument" child of THAT beneath it
(doc.Documents.Count matched the CSWDocument child count exactly in testing:
14 documents, 14 child windows). The child's title is the document's
`.name`, with a trailing " *" appended while `.Modified` is True.

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

**Title-text matching (`find_document_window`) is ambiguous with duplicate
open filenames** (issue #33, hit for real 2026-08-03/04 from a normal
Save-As-to-a-different-folder-then-reopen cycle -- two files with the same
basename in different folders both show up with the identical `.name`).
`EnumChildWindows`'s enumeration order has no guaranteed relationship to
`app.Documents`' own COM collection order, so matching by title text alone
can close a different window than the one identified (and Modified-checked)
via COM. `active_mdi_child` sidesteps this entirely: after the caller
`.Activate()`s the correct COM Document object (unambiguous -- it's a
specific object reference, not a name lookup) and brings the frame to the
OS foreground, `WM_MDIGETACTIVE` to the frame's MDIClient reports exactly
which CSWDocument child ChemDraw itself now considers active -- reflecting
live MDI activation state rather than re-deriving identity from window
text. Confirmed live: activating `chemdraw-mcp-scratch.cdxml` via COM then
querying `WM_MDIGETACTIVE` returned that exact window's hwnd, with 10 other
open documents present (4 of them sharing one duplicate name)."""
import win32api
import win32con
import win32gui

from .nudge import find_chemdraw_frames

_DOCUMENT_CLASS = "CSWDocument"
_MDI_CLIENT_CLASS = "MDIClient"


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


def find_mdi_client(frame_hwnd):
    """The single MDIClient child of one CSWFrame -- the window that owns
    MDI child (CSWDocument) activation state and answers WM_MDIGETACTIVE."""
    found = []

    def cb(h, _):
        try:
            if win32gui.GetClassName(h) == _MDI_CLIENT_CLASS:
                found.append(h)
        except Exception:
            pass

    try:
        win32gui.EnumChildWindows(frame_hwnd, cb, None)
    except Exception:
        pass
    return found[0] if found else None


def active_mdi_child(frame_hwnd):
    """The hwnd of whichever CSWDocument child is CURRENTLY ChemDraw's
    active MDI child, via the standard WM_MDIGETACTIVE message to the
    frame's MDIClient. Unlike find_document_window, this never matches on
    window title text -- so it stays correct even when two or more open
    documents share the exact same `.name` (see this module's docstring on
    issue #33). The caller is responsible for first making the INTENDED
    document ChemDraw's active one (COM `.Activate()` + bring_to_foreground)
    before calling this -- it only reports whichever child is active right
    now, it does not choose one. Returns None if no MDIClient/active child
    is found (e.g. zero documents open)."""
    client = find_mdi_client(frame_hwnd)
    if client is None:
        return None
    try:
        # WM_MDIGETACTIVE's return value IS the active child's hwnd
        # directly (not packed into a word) -- confirmed live against a
        # real 11-document ChemDraw session, 4 of them sharing one
        # duplicate name, and matched chemdraw_list_documents' own
        # "active" field exactly.
        hwnd = win32gui.SendMessage(client, win32con.WM_MDIGETACTIVE, 0, 0)
    except Exception:
        return None
    return hwnd or None
