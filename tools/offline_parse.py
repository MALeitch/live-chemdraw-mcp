"""Offline (no COM, no live ChemDraw) CDXML parsing tools.

The only tool module in this connector whose register() doesn't actually
use `bridge` -- chemdraw_parse_cdxml_file works entirely from
chemdraw_connector.domain.cdxml_document, a pure-Python parser, so it
runs with ChemDraw fully closed. `bridge` is still accepted (register(mcp,
bridge) is the shared signature every tool module uses) but intentionally
unused here, not an oversight."""
import os

from chemdraw_connector.domain import cdxml_document
from chemdraw_connector.errors import InvalidInputError

from ._common import as_json


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_parse_cdxml_file(path: str) -> str:
        """Read a .cdxml file's structures/captions/reactions/arrows/
        brackets into structured JSON, entirely offline -- no live
        ChemDraw connection needed, works even if ChemDraw is closed.

        Only .cdxml (already XML) is supported. For a .cdx file, first
        run chemdraw_convert_cdx_cdxml(input_path, output_path) to get a
        .cdxml, then parse that -- that one call does need ChemDraw open
        (it's a real binary format, not something this connector parses
        from scratch), but this tool itself never does.

        Formula is computed via RDKit from each structure's parsed atom/
        bond graph (correctly accounts for implicit hydrogens/
        aromaticity, not just a raw atom count) and is `null` with a
        `formula_note` for any structure containing a contracted/
        nickname atom (e.g. "Ph", "Boc") -- a nickname's true formula
        lives in ChemDraw's own database, not in the CDXML export, so
        this never guesses at one.

        Reactions are only reported for structures/arrows ChemDraw's own
        reaction-scheme tooling wrapped in a native <scheme><step> element
        (this is what chemdraw_make_reaction_scheme/
        chemdraw_make_reaction_route produce, and what ChemDraw's own
        "Insert Reaction" tooling produces) -- a loose hand-drawn arrow
        with no such wrapper is still parsed as a structure/arrow, just
        not grouped into a `reactions` entry.

        Result shape matches chemdraw_get_document_state's own live
        output (structures/captions/violations/page_bounds), plus
        `reactions`, `arrows`, and `brackets` (offline-only fields with
        no live-path equivalent). `non_structure_units` covers the same
        wrapper-duplicate/decoration-group exclusions the live path
        applies, via the same domain/canvas.py classification logic,
        unchanged."""
        if not os.path.exists(path):
            raise InvalidInputError(
                f"No file found at {path!r}. Check the path and retry."
            )
        if not os.path.isfile(path):
            raise InvalidInputError(
                f"{path!r} exists but is not a file (e.g. a directory). "
                "Pass the path to the .cdxml file itself."
            )
        if os.path.splitext(path)[1].lower() != ".cdxml":
            raise InvalidInputError(
                f"{path!r} is not a .cdxml file. This tool only parses "
                "already-XML .cdxml -- for a .cdx (binary) file, first "
                "call chemdraw_convert_cdx_cdxml(input_path, output_path) "
                "to get a .cdxml, then parse that."
            )
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        try:
            result = cdxml_document.parse_document(text)
        except Exception as exc:
            raise InvalidInputError(
                f"Could not parse {path!r} as CDXML: {exc}. Check the "
                "file is a well-formed .cdxml export (e.g. from "
                "chemdraw_export_cdxml or ChemDraw's own Save As)."
            )
        return as_json(result)
