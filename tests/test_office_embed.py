import base64
import zipfile

import pytest

from chemdraw_connector.domain import office_embed
from chemdraw_connector.errors import InvalidInputError

# A real, complete, working ChemDraw OLE embed (6144 bytes), extracted from
# a genuine PowerPoint file and verified live to open correctly in
# ChemDraw. Its CONTENTS stream is 2043 bytes starting with the CDX magic
# header "VjCD0100". Hand-forging a valid Compound File Binary blob from
# scratch is a real binary-format task, not attempted here -- these are
# real captured bytes, base64-inlined so the test has no external file
# dependency once written.
REAL_OLE_B64 = (
    "0M8R4KGxGuEAAAAAAAAAAAAAAAAAAAAAPgADAP7/CQAGAAAAAAAAAAAAAAABAAAAAgAAAAAAAAAAEAAABQAAAAEAAAD+////"
    "AAAAAAMAAAD/////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "///////////9/////v//////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "/////////////////////1IAbwBvAHQAIABFAG4AdAByAHkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAWAAUA////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/v///wAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD/////"
    "//////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAP///////////////wAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAUgBvAG8AdAAgAEUAbgB0AHIAeQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAABYABQD//////////wEAAAAhbbpBLqDOEY/ZACCv0fIMAAAAAAAAAAAAAAAAcFOfBvkd3QEGAAAA"
    "QAkAAAAAAAABAEMAbwBtAHAATwBiAGoAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "EgACAQMAAAACAAAA/////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACIAAACJAAAAAAAAAEMATwBOAFQA"
    "RQBOAFQAUwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAASAAIB/////wQAAAD/////"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAPsHAAAAAAAAAQBPAGwAZQAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAoAAgH///////////////8AAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAFAAAAAAAAAD//////////wQAAAD9/////v////7///8HAAAACAAAAAkAAAAKAAAA"
    "/v//////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "/////////////////////////////////////////////////////wIATwBsAGUAUAByAGUAcwAwADAAMAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAYAAIA////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAADAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAD///////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAP///////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA////////////////"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/v////7///8DAAAABAAAAAUAAAAGAAAA"
    "BwAAAAgAAAAJAAAACgAAAAsAAAAMAAAADQAAAA4AAAAPAAAAEAAAABEAAAASAAAAEwAAABQAAAAVAAAAFgAAABcAAAAYAAAA"
    "GQAAABoAAAAbAAAAHAAAAB0AAAAeAAAAHwAAACAAAAAhAAAA/v///yMAAAAkAAAA/v//////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////////AwAAAAQAAAABAAAA"
    "/////wIAAAAAAAAAAAAAAAAAAAAAAAAATkFOSQAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAgAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAFZqQ0QwMTAwBAMCAQAAAAAAAAAAAAAAgAAAAAADABYA"
    "AABDaGVtRHJhdyAyNi4wLjAuNjE0MQgADwAAAGNvbnZlcnRlZC5jZHgEAhAAbQAkAP//IwA9G1QAdEphAAEJCAA0M5X/AACW"
    "/QIJCAAAANwCAAAoAg0IAQABCAcBAAE6BAEAATsEAQAARQQBAAE8BAEAAEoEAQAADAYBAAEPBgEAAQ0GAQAAQgQBAABDBAEA"
    "AEQEAQAACggIAAMAYADIAAMACwgIAAMAAADIAAMACQgEAACAAgAICAQAmZkBAAcIBACZmQAABggEAAAAAgAFCAQAZmYOAAQI"
    "AgC0AAMIBAAAAHgAIwgBAAUMCAEAACgIAQABKQgBAAEqCAEAATIIAQAANQgBAAArCAEAKAIIEAAAACQAAAAkAAAAJAAAACQA"
    "AQMCAAAAAgMCAAEAAAMyAAgA////////AAAAAAAA//8AAAAA/////wAAAAD//wAAAAD/////AAAAAP////8AAP//AAEPAAAA"
    "AQADAOQEBQBBcmlhbAAIeAAAAwAAASABIAAAAAALZgig/4T/iAvjCRgDZwUnA/wAAgAAASABIAAAAAALZgigAAEAAABkAAAA"
    "AQABAQEAAAABJw8AAQABAAAAAAAAAAAAAAAAAAIAGQGQAAAAAABgAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAC0CwIAAAC1"
    "CxQAAABDaGVtaWNhbCBmb3JtdWxhOiC2Cw4AAABFeGFjdCBtYXNzOiC3CxQAAABNb2xlY3VsYXIgd2VpZ2h0OiC4CwcAAABt"
    "L3o6ILkLFgAAAEVsZW1lbnRhbCBhbmFseXNpczogugsRAAAAQm9pbGluZyBwb2ludDoguwsRAAAATWVsdGluZyBwb2ludDog"
    "vAsRAAAAQ3JpdGljYWwgdGVtcDogvQsRAAAAQ3JpdGljYWwgcHJlczogvgsQAAAAQ3JpdGljYWwgdm9sOiC/CxAAAABHaWJi"
    "cyBlbmVyZ3k6IMALCQAAAExvZyBQOiDBCwYAAABNUjogwgsPAAAASGVucnkncyBsYXc6IMMLEAAAAEhlYXQgb2YgZm9ybTog"
    "xAsIAAAAdFBTQTogxQsJAAAAQ0xvZ1A6IMYLBwAAAENNUjogxwsIAAAATG9nUzogyAsHAAAAcEthOiDJCwIAAADKCwIAAAAL"
    "DAIAAQAKDAEAAAkMAQAADAwFAAAAKCMpMwgJAAAAZ3JhcGhpYwGAHwAAAAQCEAAAAAAAAAAAAADAzwIAABwCFggEAAAAJAAY"
    "CAQAAAAkABkIAAAQCAIAAQAPCAIAAQADgBUAAAAEAhAAbQAkAP//IwA9G1QAdEphAAoAAgABAASAAQAAAAACCABcjygAwrVJ"
    "AAoAAgACAAIEAgAIACsEAgAAAEgEAAA3BAEAAU0EBAABAAAABoAAAAAAAAIIAMJ1LAAi0kUABAIQAG0AJAAi0kUAXI8sAGKZ"
    "TQAjCAEAAAIHAgAAAAAHDQABAAAAAwBgAMgAAABPCQcNAAEAAAADAGAAyAAAAE8AAAAABIACAAAAAAIIAML1NgDCtUkACgAC"
    "AAMANwQBAAFNBAQAAgAAAAAABIADAAAAAAIIAPUoPgAULlYACgACAAQAAgQCAAgAKwQCAAEASAQAADcEAQABTQQEAAMAAAAG"
    "gAAAAAAAAggAWw9CAHRKUgAEAhAABpo5AHRKUgD1KEIAdEphACMIAQAAAgcCAAAABQcBAAEABw4AAQAAAAMAYADIAAAAT0gJ"
    "Bw4AAQAAAAMAYADIAAAAT0gAAAAABIAEAAAAAAIIAPUoPgBwPT0ACgACAAUANwQBAAFNBAQABAAAAAAABIAFAAAAAAIIAML1"
    "NgAexTAACgACAAYANwQBAAFNBAQABQAAAAAABIAGAAAAAAIIAPUoPgDMTCQACgACAAcANwQBAAFNBAQABgAAAAAABIAHAAAA"
    "AAIIAFyPTADMTCQACgACAAgANwQBAAFNBAQABwAAAAAABIAIAAAAAAIIAI/CUwAexTAACgACAAkANwQBAAFNBAQACAAAAAAA"
    "BIAJAAAAAAIIAFyPTABwPT0ACgACAAoANwQBAAFNBAQACQAAAAAABYALAAAACgACAAsABAYEAAEAAAAFBgQAAgAAAAAGAgAC"
    "AAoGAQABAAAFgAwAAAAKAAIADAAEBgQAAgAAAAUGBAADAAAACgYBAAEAAAWADQAAAAoAAgANAAQGBAACAAAABQYEAAQAAAAK"
    "BgEAAQAABYAOAAAACgACAA4ABAYEAAQAAAAFBgQABQAAAAAGAgACAAoGAQABCwYQABMAAAANAAAAAAAAAA8AAAAAAAWADwAA"
    "AAoAAgAPAAQGBAAFAAAABQYEAAYAAAAKBgEAAQAABYAQAAAACgACABAABAYEAAYAAAAFBgQABwAAAAAGAgACAAoGAQABCwYQ"
    "AA8AAAAAAAAAAAAAABEAAAAAAAWAEQAAAAoAAgARAAQGBAAHAAAABQYEAAgAAAAKBgEAAQAABYASAAAACgACABIABAYEAAgA"
    "AAAFBgQACQAAAAAGAgACAAoGAQABCwYQABEAAAAAAAAAAAAAABMAAAAAAAWAEwAAAAoAAgATAAQGBAAEAAAABQYEAAkAAAAK"
    "BgEAAQAAEYAAAAAAAA0CAAMAEQABAAAIAAkAY2xhdWRlX2lkBQ0RAAAAY2xhdWRlLWVmZTZhNzdhAAAAAAAAAAAAAAAAAAAA"
    "AQD+/wMKAAD/////IW26QS6gzhGP2QAgr9HyDBsAAABDUyBDaGVtRHJhdyA2NC1iaXQgRHJhd2luZwAcAAAAQ2hlbURyYXcg"
    "SW50ZXJjaGFuZ2UgRm9ybWF0ABoAAABDaGVtRHJhd194NjQuRG9jdW1lbnQuNi4wAPQ5snEAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)
REAL_OLE_BYTES = base64.b64decode(REAL_OLE_B64)

CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    "</Types>"
)


def _write_zip(tmp_path, filename, files):
    """files: {zip_path: str_or_bytes}. Writes a real zip to tmp_path and
    returns its str path (find_embedded_cdx's contract is a path, not a
    file-like object)."""
    path = tmp_path / filename
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(name, data)
    return str(path)


# --------------------------------------------------------------- PPTX ----

_OLE_REL = '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/oleObject1.bin"/>'


def _pptx_files(embed_on_slide3=True, corrupt=False):
    files = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "ppt/presentation.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<p:sldIdLst>"
            '<p:sldId id="256" r:id="rId2"/>'  # -> slide2.xml, position 1
            '<p:sldId id="257" r:id="rId3"/>'  # -> slide3.xml, position 2
            '<p:sldId id="258" r:id="rId1"/>'  # -> slide1.xml, position 3
            "</p:sldIdLst>"
            "</p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide3.xml"/>'
            "</Relationships>"
        ),
        "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        "ppt/slides/slide2.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        "ppt/slides/slide3.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
    }
    if embed_on_slide3:
        files["ppt/slides/_rels/slide3.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + _OLE_REL
            + "</Relationships>"
        )
        blob = REAL_OLE_BYTES[:100] if corrupt else REAL_OLE_BYTES
        files["ppt/embeddings/oleObject1.bin"] = blob
    return files


def test_pptx_slide_index_resolved_via_presentation_order_not_filename(tmp_path):
    # The embedding sits on ppt/slides/slide3.xml (file-order suffix "3"),
    # but presentation.xml's sldIdLst puts that slide in DISPLAY position
    # 2 (slide2.xml is 1, slide3.xml is 2, slide1.xml is 3). A correct
    # implementation must report index=2, proving it resolved real
    # presentation order rather than trusting the "slideN.xml" filename.
    path = _write_zip(tmp_path, "deck.pptx", _pptx_files())
    results = office_embed.find_embedded_cdx(path)
    assert len(results) == 1
    entry = results[0]
    assert entry["embedding_path"] == "ppt/embeddings/oleObject1.bin"
    assert entry["cdx_bytes"].startswith(b"VjCD0100")
    assert entry["location"] == {
        "kind": "slide", "index": 2, "sheet_name": None, "cell": None,
    }


def test_pptx_with_no_embeddings_returns_empty_list(tmp_path):
    path = _write_zip(tmp_path, "empty.pptx", _pptx_files(embed_on_slide3=False))
    assert office_embed.find_embedded_cdx(path) == []


def test_pptx_corrupt_ole_blob_is_skipped_not_fatal(tmp_path):
    # A second, valid embedding on slide1 alongside the corrupted one on
    # slide3 proves a bad CONTENTS stream on ONE embedding doesn't abort
    # the rest of the scan.
    files = _pptx_files(corrupt=True)
    files["ppt/slides/_rels/slide1.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/oleObject2.bin"/>'
        "</Relationships>"
    )
    files["ppt/embeddings/oleObject2.bin"] = REAL_OLE_BYTES
    path = _write_zip(tmp_path, "partial.pptx", files)

    results = office_embed.find_embedded_cdx(path)

    assert len(results) == 1
    assert results[0]["embedding_path"] == "ppt/embeddings/oleObject2.bin"
    assert results[0]["location"]["index"] == 3  # slide1.xml is display position 3


# --------------------------------------------------------------- DOCX ----

def test_docx_index_reflects_body_order_not_relationship_id_order(tmp_path):
    # rId3's <w:OLEObject> reference appears FIRST in the body, rId2's
    # SECOND -- the reverse of their numeric r:id order -- to prove index
    # tracks document body order, not relationship id or filename order.
    files = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document '
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<w:body>"
            '<w:p><w:r><w:object><w:OLEObject w:Type="Embed" r:id="rId3"/></w:object></w:r></w:p>'
            '<w:p><w:r><w:object><w:OLEObject w:Type="Embed" r:id="rId2"/></w:object></w:r></w:p>'
            "</w:body>"
            "</w:document>"
        ),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/oleObject1.bin"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/oleObject2.bin"/>'
            "</Relationships>"
        ),
        "word/embeddings/oleObject1.bin": REAL_OLE_BYTES,
        "word/embeddings/oleObject2.bin": REAL_OLE_BYTES,
    }
    path = _write_zip(tmp_path, "letter.docx", files)

    results = office_embed.find_embedded_cdx(path)

    assert len(results) == 2
    assert results[0]["embedding_path"] == "word/embeddings/oleObject2.bin"
    assert results[0]["location"] == {
        "kind": "position", "index": 1, "sheet_name": None, "cell": None,
    }
    assert results[1]["embedding_path"] == "word/embeddings/oleObject1.bin"
    assert results[1]["location"]["index"] == 2


# --------------------------------------------------------------- XLSX ----

def test_xlsx_sheet_name_and_anchor_cell_resolved(tmp_path):
    files = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
            "</sheets>"
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>'
        ),
        "xl/worksheets/_rels/sheet1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/oleObject1.bin"/>'
            "</Relationships>"
        ),
        # col 5 (0-based) -> "F", row 9 (0-based) -> "10" => anchor cell F10.
        # No r:id anywhere on the anchor's shape (real files vary on this --
        # see office_embed._xlsx_drawing_ole_anchors docstring), exercising
        # the "one leftover anchor, one leftover oleObject rel" fallback
        # pairing rather than a direct r:id match.
        #
        # Shape matches a REAL Excel-produced drawing1.xml byte-for-byte in
        # structure (captured live, not guessed): the ENTIRE <xdr:
        # twoCellAnchor> -- <xdr:from> included -- sits inside <mc:
        # AlternateContent><mc:Choice>, not as a direct child of <xdr:wsDr>.
        # An earlier version of this fixture nested only the inner shape
        # metadata in AlternateContent, which let a real bug slip through
        # pytest: office_embed._xlsx_drawing_ole_anchors used a
        # non-recursive findall("xdr:twoCellAnchor") that silently found
        # zero anchors in every real file (anchor two levels deep, not a
        # direct child), while still passing against the old, structurally
        # wrong fixture. Caught only by live-testing against a real
        # Excel-produced .xlsx, not by this test -- fixed here so the
        # regression is actually covered going forward.
        "xl/drawings/drawing1.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<xdr:wsDr '
            'xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            '<mc:AlternateContent>'
            '<mc:Choice xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main" Requires="a14">'
            '<xdr:twoCellAnchor editAs="oneCell">'
            "<xdr:from><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>9</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
            "<xdr:to><xdr:col>7</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>12</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
            '<xdr:sp macro="" textlink="">'
            '<xdr:nvSpPr><xdr:cNvPr id="2" name="Object 1" hidden="1"/></xdr:nvSpPr>'
            "</xdr:sp>"
            "<xdr:clientData/>"
            "</xdr:twoCellAnchor>"
            "</mc:Choice>"
            "<mc:Fallback/>"
            "</mc:AlternateContent>"
            "</xdr:wsDr>"
        ),
        "xl/embeddings/oleObject1.bin": REAL_OLE_BYTES,
    }
    path = _write_zip(tmp_path, "sheet.xlsx", files)

    results = office_embed.find_embedded_cdx(path)

    assert len(results) == 1
    entry = results[0]
    assert entry["embedding_path"] == "xl/embeddings/oleObject1.bin"
    assert entry["cdx_bytes"].startswith(b"VjCD0100")
    assert entry["location"] == {
        "kind": "sheet", "index": None, "sheet_name": "Sheet1", "cell": "F10",
    }


def test_xlsx_absolute_anchor_reports_null_cell_without_stealing_fallback_pairing(tmp_path):
    # Regression for DEBUG_REPORT.md M-5 (2026-07-30, fixed 2026-07-31):
    # xdr:absoluteAnchor was collected by neither findall, so its
    # embedding's relationship id stayed an unresolved "leftover" -- which
    # could wrongly absorb an UNRELATED unmatched cell via the
    # single-leftover pairing fallback (see
    # office_embed._xlsx_drawing_ole_anchors's own docstring on that
    # fallback). NOT reproduced against a real absolutely-anchored
    # ChemDraw embedding (none available) -- this fixture is a static
    # OOXML-spec construction, not a live capture, unlike the sibling
    # test above.
    #
    # Two real OLE embeddings: one at a normal cell anchor (rId2, direct
    # r:id match, F10) and one via absoluteAnchor (rId3, direct r:id
    # match, no cell by definition). A third, UNRELATED shape (rId99, not
    # an OLE relationship at all) sits at cell B2 with no matching
    # oleObject rel of its own -- before the fix, rId3 would have stayed
    # in `remaining_rels` and wrongly picked up B2 via the fallback.
    files = {
        "[Content_Types].xml": CONTENT_TYPES_XML,
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
            "</sheets>"
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>'
        ),
        "xl/worksheets/_rels/sheet1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/oleObject1.bin"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/oleObject2.bin"/>'
            "</Relationships>"
        ),
        "xl/drawings/drawing1.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<xdr:wsDr '
            'xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<xdr:twoCellAnchor editAs="oneCell">'
            "<xdr:from><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>9</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
            "<xdr:to><xdr:col>7</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>12</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
            '<xdr:sp macro="" textlink="">'
            '<xdr:nvSpPr><xdr:cNvPr id="2" name="Object 1" r:id="rId2"/></xdr:nvSpPr>'
            "</xdr:sp>"
            "<xdr:clientData/>"
            "</xdr:twoCellAnchor>"
            "<xdr:absoluteAnchor>"
            '<xdr:pos x="3200400" y="1600200"/>'
            '<xdr:ext cx="1828800" cy="1143000"/>'
            '<xdr:sp macro="" textlink="">'
            '<xdr:nvSpPr><xdr:cNvPr id="3" name="Object 2" r:id="rId3"/></xdr:nvSpPr>'
            "</xdr:sp>"
            "<xdr:clientData/>"
            "</xdr:absoluteAnchor>"
            '<xdr:twoCellAnchor editAs="oneCell">'
            "<xdr:from><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
            "<xdr:to><xdr:col>2</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>2</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
            '<xdr:sp macro="" textlink="">'
            '<xdr:nvSpPr><xdr:cNvPr id="4" name="Unrelated Shape" r:id="rId99"/></xdr:nvSpPr>'
            "</xdr:sp>"
            "<xdr:clientData/>"
            "</xdr:twoCellAnchor>"
            "</xdr:wsDr>"
        ),
        "xl/embeddings/oleObject1.bin": REAL_OLE_BYTES,
        "xl/embeddings/oleObject2.bin": REAL_OLE_BYTES,
    }
    path = _write_zip(tmp_path, "sheet.xlsx", files)

    results = office_embed.find_embedded_cdx(path)

    assert len(results) == 2
    by_path = {r["embedding_path"]: r for r in results}
    assert by_path["xl/embeddings/oleObject1.bin"]["location"]["cell"] == "F10"
    # The absoluteAnchor embedding must report cell=None -- the honest
    # answer, since it has no cell -- NOT "B2" (the unrelated shape's
    # cell, which the pre-fix fallback would have wrongly assigned it).
    assert by_path["xl/embeddings/oleObject2.bin"]["location"]["cell"] is None


# ------------------------------------------------------------- generic ----

def test_non_zip_file_raises_invalid_input_error(tmp_path):
    path = tmp_path / "not_a_zip.pptx"
    path.write_text("this is plain text, not a zip archive at all")

    with pytest.raises(InvalidInputError):
        office_embed.find_embedded_cdx(str(path))


def test_missing_file_raises_invalid_input_error(tmp_path):
    with pytest.raises(InvalidInputError):
        office_embed.find_embedded_cdx(str(tmp_path / "does_not_exist.pptx"))


def test_zip_without_content_types_raises_invalid_input_error(tmp_path):
    # A real zip, but not an Office Open XML package (no [Content_Types].xml).
    path = _write_zip(tmp_path, "random.zip", {"readme.txt": "hello"})
    with pytest.raises(InvalidInputError):
        office_embed.find_embedded_cdx(path)
