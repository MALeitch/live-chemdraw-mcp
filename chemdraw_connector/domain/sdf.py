"""Pure-Python SDF/molfile record splitting -- no COM, no I/O."""


def split_sdf_records(text):
    """Split raw SDF text into individual molfile blocks, one per
    $$$$-terminated record, discarding each record's trailing
    ">  <tag>"-style data fields (ChemDraw's molfile importer only wants
    the connection table through "M  END", not the SDF data block).

    Blank/whitespace-only records (a trailing blank chunk after the last
    $$$$, or an empty file) are dropped. A record with no "M  END" marker
    is kept as-is (stripped) rather than dropped, so a malformed record
    still reaches the caller as a real record that fails loudly on
    insertion, instead of silently vanishing."""
    records = []
    for raw in text.split("$$$$"):
        if not raw.strip():
            continue
        # Every chunk after the first starts with the newline that trailed
        # the previous "$$$$" line -- left in place, that blank line shifts
        # every subsequent line by one, which breaks RDKit's molfile parser
        # (title/program/comment/counts are fixed-position, lines 1-4).
        # Strip it BEFORE searching for "M  END" so the slice below starts
        # exactly at the record's real title line, not one line early.
        raw = raw.lstrip("\n")
        end = raw.find("M  END")
        records.append(raw[:end + len("M  END")] if end != -1 else raw.strip())
    return records
