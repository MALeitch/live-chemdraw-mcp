"""Chemical-formula subscript formatting for reagent/condition text.

ChemDraw's Caption.Styles collection has no way to add a new formatted run
via COM (no Add method -- it's read-only for structural edits, confirmed
live), so per-character rich-text formatting (the way ChemDraw's own UI
would let a user select "3" in "PPh3" and hit the subscript button) isn't
reachable by editing an existing caption object. It IS reachable by
constructing the caption from scratch as CDXML text and inserting that
directly (the same Data-property-put mechanism used for structures) --
confirmed live that a CDX text run's `face` attribute has real bits for
this: face="32" renders as genuine subscript (smaller font, shifted
baseline -- not a fixed-glyph substitute) and face="64" as superscript,
both distinct from the bold(1)/italic(2)/underline(4) bits used elsewhere
in this codebase for caption bolding.
"""
import re
from xml.sax.saxutils import escape

_FORMULA_DIGITS = re.compile(r"(?<=[A-Za-z)])\d+")
FACE_SUBSCRIPT = 32
FACE_SUPERSCRIPT = 64


def split_subscript_runs(text):
    """Split text into (segment, is_subscript) runs, wherever a digit run
    immediately follows a letter or ')' -- the position a chemical-formula
    subscript occurs in (the "2" in "K2CO3", the "3" in "PPh3", the "4" in
    "Pd(PPh3)4"). Leaves any other number as a plain (non-subscript)
    segment: equivalents ("2.5 equiv"), temperatures ("80 °C", "-78 °C"),
    times ("4 h"), step numbers ("1)", "2)"), and hyphenated names with a
    trailing number ("18-crown-6") all have a space, hyphen, start-of-
    string, or other non-letter/non-')' character immediately before the
    digit, so none of them match. Not a full chemistry parser -- a
    heuristic tuned against realistic reagent-line notation, not a formula
    validator (a bare compound name that happens to look like a formula,
    e.g. "T3P", will still be misread as one -- no heuristic on the text
    alone can tell those apart)."""
    runs = []
    pos = 0
    for m in _FORMULA_DIGITS.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], False))
        runs.append((m.group(), True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    return runs


def build_text_cdxml(runs, x, y, width, height, font=3, size=10.0, color=0):
    """A minimal single-page CDXML document containing one <t> text object
    with one <s> run per (text, is_subscript) segment from
    split_subscript_runs, using FACE_SUBSCRIPT for the subscript runs.

    x/y/width/height are only a placeholder BoundingBox/position for the
    CDXML import step -- CDXML-import-time placement isn't trustworthy
    (confirmed live elsewhere in this codebase: inserted structures need
    an explicit post-insert Move() too), so the caller must re-read the
    actual resulting Caption object's bounds and reposition it explicitly
    after insertion, exactly like every other insertion in this codebase
    already does. font/size/color match ChemDraw's own caption defaults
    (CaptionFont=3, CaptionSize=10 in this project's documents) unless
    overridden."""
    spans = "".join(
        f'<s font="{font}" size="{size}"'
        + (f' face="{FACE_SUBSCRIPT}"' if is_sub else "")
        + f'>{escape(segment)}</s>'
        for segment, is_sub in runs if segment
    )
    return (
        '<?xml version="1.0" ?>'
        '<!DOCTYPE CDXML SYSTEM '
        '"https://static.chemistry.revvitycloud.com/cdxml/CDXML.dtd">'
        '<CDXML><page>'
        f'<t p="{x} {y}" BoundingBox="{x} {y - height} {x + width} {y}">'
        f'{spans}</t>'
        '</page></CDXML>'
    )
