"""Structured errors surfaced to Claude as readable tool errors."""


class ChemDrawError(Exception):
    """Base for all connector errors."""


class ChemDrawNotAvailableError(ChemDrawError):
    """ChemDraw could not be attached to or launched."""


class ChemDrawBlockedError(ChemDrawError):
    """A COM call timed out and an automatic nudge didn't clear it — ChemDraw
    is showing a modal dialog, or is mid-edit in a way Escape didn't resolve."""

    def __init__(self, detail=""):
        super().__init__(
            "ChemDraw is not responding to automation and an automatic nudge "
            "did not clear it. Check the ChemDraw window for an open dialog box "
            "to dismiss, or press Escape / click an empty part of the canvas to "
            "finish any in-progress text edit, then retry. " + detail
        )


class TargetNotFoundError(ChemDrawError):
    """An object_id no longer resolves to anything in the document."""

    def __init__(self, object_id):
        super().__init__(
            f"Object '{object_id}' no longer exists in the document — it may have "
            "been deleted or modified by hand. Call chemdraw_get_document_state to "
            "see what is currently on the page."
        )


class NothingSelectedError(ChemDrawError):
    def __init__(self):
        super().__init__(
            "Nothing is selected in ChemDraw. Select a structure in the ChemDraw "
            "window (or pass an object_id as the target) and retry."
        )


class InvalidInputError(ChemDrawError):
    """Chemically invalid input caught before or during a ChemDraw call."""


class UnexpectedConnectorError(ChemDrawError):
    """A non-COM, non-ChemDrawError exception escaped a mutating call — a
    plain bug (bad input handling, an unexpected AttributeError, an RDKit
    exception, ...) rather than a normal chemistry/COM failure. Wrapped so
    the client still gets the connector's usual readable-error vocabulary
    instead of a raw Python traceback string, without hiding what actually
    happened."""

    def __init__(self, exc):
        super().__init__(
            f"Unexpected connector error ({type(exc).__name__}): {exc}. "
            "This looks like a bug in the connector itself rather than a "
            "normal chemistry/COM problem — the ChemDraw window and any "
            "in-progress edit should be unaffected, but the operation did "
            "not complete."
        )
