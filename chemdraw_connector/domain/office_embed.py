"""Locate embedded ChemDraw OLE objects inside Office Open XML packages
(.pptx/.docx/.xlsx) and report where each one lives in the document.

Office Open XML files are plain ZIP archives. A ChemDraw structure pasted
or inserted into PowerPoint/Word/Excel becomes an OLE ("Compound File
Binary") blob stored at a predictable path inside that zip --
`ppt/embeddings/oleObjectN.bin`, `word/embeddings/oleObjectN.bin`, or
`xl/embeddings/oleObjectN.bin` -- with the actual CDX bytes inside that
blob's own `CONTENTS` stream (opened via olefile). This module only FINDS
and extracts those raw bytes plus their document location; it does not
touch ChemDraw and never validates the chemistry -- a separate stage feeds
`cdx_bytes` into real ChemDraw conversion and decides per-embedding
validity there. That is also why an unparsable individual OLE blob is
silently skipped here rather than raising: this function's contract is
"best-effort find what's on disk", not "confirm it's usable".

Ground truth below was verified live (this session, against real files
ChemDraw actually produced) unless flagged otherwise:

PPTX (verified against a real 10-embedding presentation):
  - `ppt/presentation.xml`'s <p:sldIdLst> lists <p:sldId r:id="rIdN"/> in
    real display order -- this is NOT necessarily the same order as the
    numeric suffixes on ppt/slides/slideM.xml (slides can be reordered
    after creation without the underlying filenames changing).
  - `ppt/_rels/presentation.xml.rels` maps each rIdN to slides/slideM.xml.
  - Cross-referencing those two gives the true 1-based presentation-order
    slide number for each slideM.xml.
  - `ppt/slides/_rels/slideM.xml.rels` has oleObject relationships pointing
    at `../embeddings/oleObjectN.bin`, tying an embedding to its slide.

XLSX (verified against a real embedding, including its exact anchor cell):
  - `xl/workbook.xml`'s <sheet name=".." r:id="rIdN"/> gives real sheet
    names.
  - `xl/_rels/workbook.xml.rels` maps rIdN to worksheets/sheetM.xml.
  - `xl/worksheets/_rels/sheetM.xml.rels` has the sheet's drawing
    relationship AND its oleObject relationship(s) as SIBLING entries in
    the same rels file -- no extra indirection needed to find which
    embedding belongs to which sheet.
  - `xl/drawings/drawingK.xml` anchors each embedded object via
    <xdr:from><xdr:col>/<xdr:row> (0-based); real files wrap this in an
    <mc:AlternateContent> with mc:Choice/mc:Fallback variants, so both are
    checked for an <xdr:from>. Column index -> letters, row index + 1 ->
    row number, per A1 notation. If a cell can't be confidently resolved,
    "cell" is None rather than guessed.

DOCX (NOT live-verified this session -- standard OOXML convention, medium
confidence only):
  - Single `word/_rels/document.xml.rels` maps r:ids straight to
    `embeddings/oleObjectN.bin` (no per-page indirection, unlike pptx).
  - `word/document.xml` body is walked in document order; each oleObject
    reference encountered gets a 1-based ordinal `index`. Word computes
    pagination at render time, not in the raw XML, so no page number is
    ever invented here.

Zero COM imports; pure zip/XML/OLE parsing.
"""
import io
import os
import re
import string
import xml.etree.ElementTree as ET
import zipfile

import olefile

from chemdraw_connector.errors import InvalidInputError

_NS = {
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}

_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OLE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
_DRAWING_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing"


def find_embedded_cdx(office_path):
    """Scan a .pptx/.docx/.xlsx for embedded ChemDraw OLE objects.

    Returns a list of {"embedding_path", "cdx_bytes", "location"} dicts,
    one per embedding found (see module docstring for per-format
    `location` shape). Does not pre-filter by whether the embedded bytes
    are actually valid ChemDraw chemistry -- that's a downstream concern.

    Raises InvalidInputError if `office_path` isn't a readable zip, or
    doesn't look like an Office Open XML package (no [Content_Types].xml).
    A single embedding whose CONTENTS stream can't be extracted (corrupt
    OLE blob, missing CONTENTS stream, etc.) is silently skipped -- it
    does not abort the rest of the scan.
    """
    try:
        zf = zipfile.ZipFile(office_path, "r")
    except (zipfile.BadZipFile, OSError, FileNotFoundError) as exc:
        raise InvalidInputError(
            f"'{office_path}' is not a readable zip/Office Open XML file: {exc}"
        )

    with zf:
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names:
            raise InvalidInputError(
                f"'{office_path}' does not look like an Office Open XML "
                "package (missing [Content_Types].xml)."
            )

        if any(n.startswith("ppt/") for n in names):
            return _scan_pptx(zf, names)
        if any(n.startswith("word/") for n in names):
            return _scan_docx(zf, names)
        if any(n.startswith("xl/") for n in names):
            return _scan_xlsx(zf, names)
        return []


def _extract_cdx_bytes(zf, embedding_path):
    """Read embedding_path's CONTENTS OLE stream, or None on any failure
    (corrupt blob, missing CONTENTS stream, not a valid OLE file at all).
    Never raises -- callers skip the entry when this returns None."""
    try:
        raw = zf.read(embedding_path)
        with olefile.OleFileIO(io.BytesIO(raw)) as ole:
            streams = {tuple(s) for s in ole.listdir()}
            if ("CONTENTS",) not in streams:
                return None
            return ole.openstream("CONTENTS").read()
    except Exception:
        return None


def _parse_rels(zf, rels_path):
    """rels_path -> {rId: target}, or {} if the rels file doesn't exist."""
    if rels_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rels_path))
    out = {}
    for rel in root.findall(f"{{{_RELS_NS}}}Relationship"):
        out[rel.get("Id")] = rel
    return out


def _rels_for(zf, part_path):
    """The _rels/*.rels sibling for a given part path, parsed to a dict of
    {Id: <Relationship> element}."""
    directory, filename = os.path.split(part_path)
    rels_path = f"{directory}/_rels/{filename}.rels" if directory else f"_rels/{filename}.rels"
    return _parse_rels(zf, rels_path)


def _resolve_target(part_path, target):
    """Relationship Target values are relative to the part's own
    directory (e.g. "../embeddings/oleObject1.bin" from
    ppt/slides/_rels/slide1.xml.rels resolves against ppt/slides/)."""
    directory = os.path.dirname(part_path)
    joined = os.path.normpath(f"{directory}/{target}").replace("\\", "/")
    return joined


# ---------------------------------------------------------------- PPTX ----

def _scan_pptx(zf, names):
    out = []

    slide_order = _pptx_slide_order(zf)  # slideM.xml path -> 1-based index

    slide_paths = sorted(
        (n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda n: slide_order.get(n, 10**9),
    )

    for slide_path in slide_paths:
        index = slide_order.get(slide_path)
        rels = _rels_for(zf, slide_path)
        for rel in rels.values():
            if rel.get("Type") != _OLE_REL_TYPE:
                continue
            embedding_path = _resolve_target(slide_path, rel.get("Target"))
            if embedding_path not in names:
                continue
            cdx_bytes = _extract_cdx_bytes(zf, embedding_path)
            if cdx_bytes is None:
                continue
            out.append({
                "embedding_path": embedding_path,
                "cdx_bytes": cdx_bytes,
                "location": {
                    "kind": "slide",
                    "index": index,
                    "sheet_name": None,
                    "cell": None,
                },
            })
    return out


def _pptx_slide_order(zf):
    """ppt/slides/slideM.xml -> 1-based presentation display order,
    resolved via presentation.xml's <p:sldIdLst> + presentation.xml.rels
    (NOT by sorting slideN.xml filenames -- see module docstring)."""
    pres_path = "ppt/presentation.xml"
    if pres_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(pres_path))
    sld_id_list = root.find("p:sldIdLst", _NS)
    if sld_id_list is None:
        return {}
    rels = _rels_for(zf, pres_path)

    order = {}
    for i, sld_id in enumerate(sld_id_list.findall("p:sldId", _NS), start=1):
        rid = sld_id.get(f"{{{_NS['r']}}}id")
        rel = rels.get(rid)
        if rel is None:
            continue
        slide_path = _resolve_target(pres_path, rel.get("Target"))
        order[slide_path] = i
    return order


# ---------------------------------------------------------------- DOCX ----

def _scan_docx(zf, names):
    doc_path = "word/document.xml"
    if doc_path not in zf.namelist():
        return []

    rels = _rels_for(zf, doc_path)
    ole_rels = {
        rid: rel for rid, rel in rels.items() if rel.get("Type") == _OLE_REL_TYPE
    }
    if not ole_rels:
        return []

    root = ET.fromstring(zf.read(doc_path))

    out = []
    index = 0
    r_id_attr = f"{{{_NS['r']}}}id"
    # Walk the body in document order; any element carrying an r:id that
    # matches a known oleObject relationship counts as one embedding
    # reference, in the order encountered.
    for elem in root.iter():
        rid = elem.get(r_id_attr)
        if rid is None or rid not in ole_rels:
            continue
        embedding_path = _resolve_target(doc_path, ole_rels[rid].get("Target"))
        if embedding_path not in names:
            continue
        cdx_bytes = _extract_cdx_bytes(zf, embedding_path)
        if cdx_bytes is None:
            continue
        index += 1
        out.append({
            "embedding_path": embedding_path,
            "cdx_bytes": cdx_bytes,
            "location": {
                "kind": "position",
                "index": index,
                "sheet_name": None,
                "cell": None,
            },
        })
    return out


# ---------------------------------------------------------------- XLSX ----

def _scan_xlsx(zf, names):
    wb_path = "xl/workbook.xml"
    if wb_path not in zf.namelist():
        return []

    root = ET.fromstring(zf.read(wb_path))
    wb_rels = _rels_for(zf, wb_path)

    sheets = []  # (sheet_name, sheetM.xml path)
    sheets_elem = root.find("main:sheets", _NS)
    if sheets_elem is None:
        return []
    for sheet in sheets_elem.findall("main:sheet", _NS):
        rid = sheet.get(f"{{{_NS['r']}}}id")
        rel = wb_rels.get(rid)
        if rel is None:
            continue
        sheet_path = _resolve_target(wb_path, rel.get("Target"))
        sheets.append((sheet.get("name"), sheet_path))

    out = []
    for sheet_name, sheet_path in sheets:
        sheet_rels = _rels_for(zf, sheet_path)

        ole_rels = [r for r in sheet_rels.values() if r.get("Type") == _OLE_REL_TYPE]
        if not ole_rels:
            continue

        drawing_rel = next(
            (r for r in sheet_rels.values() if r.get("Type") == _DRAWING_REL_TYPE),
            None,
        )
        anchors = {}
        if drawing_rel is not None:
            drawing_path = _resolve_target(sheet_path, drawing_rel.get("Target"))
            if drawing_path in names:
                ole_rel_ids = [r.get("Id") for r in ole_rels]
                anchors = _xlsx_drawing_ole_anchors(zf, drawing_path, ole_rel_ids)

        for rel in ole_rels:
            embedding_path = _resolve_target(sheet_path, rel.get("Target"))
            if embedding_path not in names:
                continue
            cdx_bytes = _extract_cdx_bytes(zf, embedding_path)
            if cdx_bytes is None:
                continue
            cell = anchors.get(rel.get("Id"))
            out.append({
                "embedding_path": embedding_path,
                "cdx_bytes": cdx_bytes,
                "location": {
                    "kind": "sheet",
                    "index": None,
                    "sheet_name": sheet_name,
                    "cell": cell,
                },
            })
    return out


def _xlsx_drawing_ole_anchors(zf, drawing_path, ole_rel_ids):
    """drawingK.xml + the sheet's own oleObject relationship ids ->
    {relationship_id: "A1"}.

    Each anchor (<xdr:twoCellAnchor>/<xdr:oneCellAnchor>) may be wrapped in
    an <mc:AlternateContent> (mc:Choice/mc:Fallback); this walks the whole
    anchor subtree for <xdr:from> and for any r:id/r:embed-style attribute,
    since real files are inconsistent about which branch carries what (see
    module docstring -- "namespaces are messy here").

    Which shape belongs to which oleObject relationship is NOT reliably
    documented by Microsoft and was only confirmed live for the common
    single-embedding-per-sheet case, where an anchor's own r:id happens to
    equal the oleObject relationship's Id. When that direct match fails
    (attribute absent, or points at something else, e.g. a VML shape id)
    but exactly one anchor and exactly one oleObject relationship are left
    over after direct matches are removed, they are paired unambiguously --
    there's nothing else it could be. Anything left ambiguous beyond that
    (more than one leftover on either side) is left unresolved, and the
    caller reports "cell": None for it rather than guessing.

    xdr:absoluteAnchor -- the third OOXML anchor type, used when a shape
    is positioned by absolute EMU offset (<xdr:pos>) rather than anchored
    to a cell -- is collected here too (NOT reproduced live, static
    reasoning only per DEBUG_REPORT.md M-5: no absolutely-anchored
    ChemDraw embedding was available to test against). It genuinely has
    no <xdr:from> and therefore no cell to report -- "cell": None is the
    correct answer for it, not a gap. What WAS a gap: without collecting
    its r:id at all, its embedding's relationship id stayed a "remaining"
    leftover below indefinitely, which could wrongly absorb an unrelated
    unmatched cell via the single-leftover pairing fallback. Recording it
    with cell=None removes that mis-attribution risk even though the
    reported cell value doesn't change."""
    root = ET.fromstring(zf.read(drawing_path))
    anchors = []  # [(rid_or_None, cell), ...]

    # .// (recursive), not a direct-children search: confirmed live that
    # real files wrap the anchor in <mc:AlternateContent><mc:Choice>...,
    # nesting <xdr:twoCellAnchor> two levels below <xdr:wsDr> rather than
    # as its direct child -- a plain (non-recursive) findall silently
    # found zero anchors in every real Excel-produced file tested,
    # despite this function's own docstring already anticipating the
    # AlternateContent wrapper.
    for anchor in list(root.findall(".//xdr:twoCellAnchor", _NS)) + list(
        root.findall(".//xdr:oneCellAnchor", _NS)
    ) + list(root.findall(".//xdr:absoluteAnchor", _NS)):
        from_elem = anchor.find("xdr:from", _NS)
        if from_elem is None:
            # xdr:absoluteAnchor (or any anchor missing <xdr:from>) --
            # record its r:id with a None cell instead of dropping it
            # from `anchors` entirely (see docstring above). Skip
            # entirely if no r:id can be found either -- an
            # unidentifiable (None, None) pair is not useful and would
            # only risk polluting the fallback's unmatched_cells list.
            rid = _find_ole_rid(anchor)
            if rid is not None:
                anchors.append((rid, None))
            continue
        cell = _xdr_from_to_a1(from_elem)
        if cell is None:
            continue
        anchors.append((_find_ole_rid(anchor), cell))

    out = {}
    unmatched_cells = []
    for rid, cell in anchors:
        if rid in ole_rel_ids:
            out[rid] = cell
        else:
            unmatched_cells.append(cell)

    remaining_rels = [rid for rid in ole_rel_ids if rid not in out]
    if len(remaining_rels) == 1 and len(unmatched_cells) == 1:
        out[remaining_rels[0]] = unmatched_cells[0]

    return out


def _find_ole_rid(anchor):
    """Search an anchor subtree for any r:id/r:embed attribute -- covers
    both a direct oleObject/control element and the mc:Fallback variant."""
    for elem in anchor.iter():
        for attr in (f"{{{_NS['r']}}}id", f"{{{_NS['r']}}}embed"):
            val = elem.get(attr)
            if val:
                return val
    return None


def _xdr_from_to_a1(from_elem):
    col = from_elem.find("xdr:col", _NS)
    row = from_elem.find("xdr:row", _NS)
    if col is None or row is None or col.text is None or row.text is None:
        return None
    try:
        col_idx = int(col.text)
        row_idx = int(row.text)
    except ValueError:
        return None
    return f"{_col_to_letters(col_idx)}{row_idx + 1}"


def _col_to_letters(col_idx):
    """0-based column index -> Excel column letters ("A", ..., "Z", "AA", ...)."""
    letters = ""
    n = col_idx + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = string.ascii_uppercase[remainder] + letters
    return letters
