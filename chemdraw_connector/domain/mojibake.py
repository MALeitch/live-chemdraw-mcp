"""Repairing UTF-8/Latin-1 double-encoding ("mojibake") in text read back
from ChemDraw over COM.

CONFIRMED LIVE: a caption containing a non-ASCII character (e.g. the degree
sign, U+00B0, in "80 °C") round-trips through Caption.Text corrupted --
what comes back over COM is TWO codepoints (U+00C2 "Â" + U+00B0 "°", i.e.
"Â°") instead of the one U+00B0 that was written. Confirmed NOT a real
ChemDraw-internal/rendering corruption: chemdraw_export_image of the exact
same caption renders one correct "°" glyph -- so ChemDraw's own storage and
rendering are fine, and something in the COM property GET path (pywin32's
BSTR marshaling, or ChemDraw's own COM accessor) is what mangles the text
on the way out. This matches the classic "UTF-8 bytes read back through a
Latin-1/cp1252 lens" mojibake pattern exactly: U+00B0 encodes to UTF-8 as
bytes C2 B0; interpreting those two bytes AS Latin-1 codepoints instead of
decoding them as UTF-8 gives exactly U+00C2 U+00B0 ("Â°"), matching what
was observed byte-for-byte.

Because the corruption is confirmed to happen on read rather than in
ChemDraw's own storage, it's safe to repair by reversing that exact
mis-interpretation: encode the mangled text back to Latin-1 bytes (undoing
the wrong "each byte is one Latin-1 codepoint" read), then decode those
bytes as UTF-8 (the correct interpretation all along). Guarded to only
apply when that round trip actually succeeds and changes something -- text
that was never corrupted this way (plain ASCII, or a genuinely different
Unicode string) either can't be encoded as Latin-1 at all (raises,
original returned unchanged) or round-trips to itself (no-op)."""


def fix_mojibake(text):
    if not text:
        return text
    try:
        repaired = text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if repaired != text else text
