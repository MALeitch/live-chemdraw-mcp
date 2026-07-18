"""Chemical-formula subscript formatting for reagent/condition text.

ChemDraw's Caption.Styles collection has no way to add a new formatted run
via COM (no Add method -- it's read-only for structural edits, confirmed
live), so per-character rich-text formatting (the way ChemDraw's own UI
would let a user select "3" in "PPh3" and hit the subscript button) isn't
reachable through this API. Unicode subscript digits render correctly in
ChemDraw's caption text directly, though (confirmed live), so this module
sidesteps the Styles limitation entirely rather than working around it.
"""
import re

_SUBSCRIPT_DIGITS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_FORMULA_DIGITS = re.compile(r"(?<=[A-Za-z)])\d+")


def subscript_formula_numbers(text):
    """Convert digit runs that immediately follow a letter or ')' --
    the position a chemical-formula subscript actually occurs in (the "2"
    in "K2CO3", the "3" in "PPh3", the "4" in "Pd(PPh3)4") -- to Unicode
    subscript digits. Leaves any other number untouched: equivalents
    ("2.5 equiv"), temperatures ("80 °C", "-78 °C"), times ("4 h"), step
    numbers ("1)", "2)"), and hyphenated names with a trailing number
    ("18-crown-6") all have a space, hyphen, start-of-string, or other
    non-letter/non-')' character immediately before the digit, so none of
    them match. Not a full chemistry parser -- a heuristic tuned against
    realistic reagent-line notation, not a formula validator."""
    return _FORMULA_DIGITS.sub(lambda m: m.group().translate(_SUBSCRIPT_DIGITS), text)
