"""Parsing for the free-text label filter used by bulk label expansion.

Zero COM imports; pure string handling so it's unit-testable without
ChemDraw.
"""


def parse_label_filter(labels):
    """labels: None/""/"all"/"*" -> None (no filtering, match every
    contracted label). Otherwise a comma-separated string, or a list/tuple
    of strings, -> a lowercase set for case-insensitive exact matching
    against the text drawn on a contracted nickname node (e.g. "Ph",
    "Boc"). Whitespace around each entry is stripped."""
    if labels is None:
        return None
    if isinstance(labels, (list, tuple)):
        items = [str(s) for s in labels]
    else:
        items = str(labels).split(",")
    items = [s.strip() for s in items if s.strip()]
    if not items or (len(items) == 1 and items[0].lower() in ("all", "*")):
        return None
    return {s.lower() for s in items}
