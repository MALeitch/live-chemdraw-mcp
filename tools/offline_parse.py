"""Offline (no COM, no live ChemDraw) CDXML/CDX parsing tools.

The only tool module in this connector whose register() doesn't actually
use `bridge` -- chemdraw_parse_cdxml_file and chemdraw_parse_cdx_file work
entirely from chemdraw_connector.domain.cdxml_document and
chemdraw_connector.domain.cdx_document, pure-Python parsers, so they
run with ChemDraw fully closed. `bridge` is still accepted (register(mcp,
bridge) is the shared signature every tool module uses) but intentionally
unused here, not an oversight.
"""
import os

from chemdraw_connector.domain import cdxml_document, cdx_document
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
        not grouped into a `reactions` entry. CONFIRMED LIVE: ChemDraw
        can ALSO wrap a completely unrelated loose arrow (e.g. one made
        with chemdraw_make_arrow, positioned nowhere near what it ends up
        paired with) in its own <scheme><step>, unprompted -- `reactions`
        reflects ChemDraw's OWN interpretation faithfully, it is NOT a
        guarantee every entry describes a real reaction the chemist
        intended. A step with empty `product_ids` (an arrow with no
        product, unusual for a real single-step scheme) is a signal to
        treat that entry as low-confidence.

        Result shape matches chemdraw_get_document_state's own live
        output (structures/captions/violations/page_bounds), plus
        `reactions`, `arrows`, and `brackets` (offline-only fields with
        no live-path equivalent). `non_structure_units` covers the same
        wrapper-duplicate/decoration-group exclusions the live path
        applies, via the same domain/canvas.py classification logic,
        unchanged.

        Content from every page in the file is included, but
        `page_bounds`/`violations.off_page` reflect only the FIRST page's
        extent (no known real ChemDraw export has more than one <page>
        element, so this has not come up in practice). `extra_pages` is
        present and non-zero only if the file actually has more than one
        <page> -- if you see it, treat off_page violations as unreliable
        for anything past the first page."""
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

    @mcp.tool()
    def chemdraw_parse_cdx_file(path: str) -> str:
        """Read a .cdx (binary ChemDraw) file's structures into structured
        JSON, entirely offline -- no live ChemDraw connection needed,
        works even if ChemDraw is closed.

        Unlike .cdxml, the binary .cdx format does not store reactions,
        captions, arrows, or brackets in a recoverable way -- only the
        molecular fragments (nodes/bonds) are parsed. This tool returns
        the same structure shape as chemdraw_parse_cdxml_file but with
        empty `captions`, `reactions`, `arrows`, `brackets`, and
        `non_structure_units` arrays, and no `page_bounds`.

        Formula is computed via RDKit from each structure's parsed atom/
        bond graph (correctly accounts for implicit hydrogens/
        aromaticity, not just a raw atom count) and is `null` with a
        `formula_note` for any structure containing a contracted/
        nickname atom (e.g. "Ph", "Boc") -- a nickname's true formula
        lives in ChemDraw's own database, not in the CDX export, so
        this never guesses at one.

        Reactions/arrows/brackets are not present in raw CDX -- if you
        need those, convert to .cdxml first via chemdraw_convert_cdx_cdxml
        (which requires ChemDraw), then use chemdraw_parse_cdxml_file.

        This tool uses the connector's native binary .cdx parser
        (chemdraw_connector.domain.cdx_binary) which achieves 99% SMILES
        recall against ChemDraw's own .cdx->.cdxml conversions (tested
        across 8 real files, 518 fragments)."""
        if not os.path.exists(path):
            raise InvalidInputError(
                f"No file found at {path!r}. Check the path and retry."
            )
        if not os.path.isfile(path):
            raise InvalidInputError(
                f"{path!r} exists but is not a file (e.g. a directory). "
                "Pass the path to the .cdx file itself."
            )
        if os.path.splitext(path)[1].lower() != ".cdx":
            raise InvalidInputError(
                f"{path!r} is not a .cdx file. This tool only parses "
                "binary .cdx -- for a .cdxml (XML) file, use "
                "chemdraw_parse_cdxml_file instead."
            )
        try:
            result = cdx_document.parse_cdx_document(path)
        except Exception as exc:
            raise InvalidInputError(
                f"Could not parse {path!r} as CDX: {exc}. Check the "
                "file is a well-formed ChemDraw binary export."
            )
        return as_json(result)