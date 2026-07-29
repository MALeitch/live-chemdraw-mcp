"""Lightweight test of the chemdraw_list_office_embeddings /
chemdraw_extract_office_embeddings tool wrappers in tools/office_embed.py.

No COM, no real Office files, no real chemdraw_connector.domain.office_embed
scanning involved: `chemdraw_connector.domain.office_embed.find_embedded_cdx`
and `chemdraw_connector.domain.cdxml_document.parse_document` are both
monkeypatched with fixtures matching their documented return shapes, and
`bridge.convert_cdx_cdxml` is a stub. `mcp` is a stub whose .tool() decorator
just captures the wrapped function, mirroring tests/test_procedure_qc.py.
"""
import json

import pytest

from chemdraw_connector.domain import cdxml_document, office_embed
from tools import office_embed as office_embed_tool


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeBridge:
    """convert_cdx_cdxml stub: writes a tiny placeholder .cdxml at
    output_path (mirroring the real bridge's SaveAs behavior of actually
    producing the output file) and records each call."""

    def __init__(self, fail_on=None, cdxml_text_by_call=None):
        self.calls = []
        self.fail_on = fail_on or set()
        self.cdxml_text_by_call = cdxml_text_by_call or {}

    def convert_cdx_cdxml(self, input_path, output_path, overwrite=False):
        self.calls.append((input_path, output_path, overwrite))
        call_index = len(self.calls) - 1
        if call_index in self.fail_on:
            raise RuntimeError(f"simulated ChemDraw conversion failure #{call_index}")
        text = self.cdxml_text_by_call.get(call_index, "<CDXML/>")
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return {
            "input_path": input_path,
            "output_path": output_path,
            "bytes": len(text),
            "reused_existing_document": False,
            "restored_active_document": None,
        }


def _register(mcp, bridge):
    office_embed_tool.register(mcp, bridge)
    return mcp.tools


def _fixture_entries():
    return [
        {
            "embedding_path": "ppt/embeddings/oleObject1.bin",
            "cdx_bytes": b"cdx-bytes-for-slide-embed",
            "location": {"kind": "slide", "index": 2, "sheet_name": None, "cell": None},
        },
        {
            "embedding_path": "ppt/embeddings/oleObject2.bin",
            "cdx_bytes": b"cdx-bytes-for-empty-paste",
            "location": {"kind": "slide", "index": 3, "sheet_name": None, "cell": None},
        },
        {
            "embedding_path": "xl/embeddings/oleObject1.bin",
            "cdx_bytes": b"cdx-bytes-that-will-fail",
            "location": {"kind": "sheet", "index": None, "sheet_name": "Sheet1", "cell": "F10"},
        },
    ]


@pytest.fixture
def patched(monkeypatch):
    """Patches office_embed.find_embedded_cdx (used by both the domain
    module reference held by tools/office_embed.py) and
    cdxml_document.parse_document, returning the fixture entries for
    inspection."""
    entries = _fixture_entries()
    monkeypatch.setattr(office_embed, "find_embedded_cdx", lambda path: entries)

    # parse_document call #0 (slide embed 1) -> real structure.
    # parse_document call #1 (slide embed 2) -> empty structures (paste placeholder).
    # call #2 never happens -- embed 3 fails during convert_cdx_cdxml.
    responses = [
        {"structures": [{"id": "s1", "smiles": "CCO"}], "captions": [], "boxes": [],
         "non_structure_units": [], "violations": {}, "page_bounds": None,
         "reactions": [], "arrows": [], "brackets": []},
        {"structures": [], "captions": [], "boxes": [],
         "non_structure_units": [], "violations": {}, "page_bounds": None,
         "reactions": [], "arrows": [], "brackets": []},
    ]
    call_count = {"n": 0}

    def fake_parse_document(text):
        i = call_count["n"]
        call_count["n"] += 1
        return responses[i]

    monkeypatch.setattr(cdxml_document, "parse_document", fake_parse_document)
    return entries


def test_list_office_embeddings_reports_byte_counts_not_raw_bytes(patched):
    mcp = _FakeMCP()
    tools = _register(mcp, _FakeBridge())
    result = json.loads(tools["chemdraw_list_office_embeddings"]("fake.pptx"))

    assert result["found"] == 3
    assert len(result["embeddings"]) == 3
    first = result["embeddings"][0]
    assert first["embedding_path"] == "ppt/embeddings/oleObject1.bin"
    assert first["location"] == {"kind": "slide", "index": 2, "sheet_name": None, "cell": None}
    assert first["bytes"] == len(b"cdx-bytes-for-slide-embed")
    # Raw bytes must never leak into the JSON response.
    assert "cdx_bytes" not in first


def test_extract_office_embeddings_success_and_empty_note(patched):
    mcp = _FakeMCP()
    bridge = _FakeBridge(fail_on={2})  # 3rd embedding (index 2) fails conversion
    tools = _register(mcp, bridge)
    result = json.loads(tools["chemdraw_extract_office_embeddings"]("fake.pptx"))

    assert result["found"] == 3
    assert len(result["results"]) == 2
    assert len(result["failed"]) == 1

    success = result["results"][0]
    assert success["embedding_path"] == "ppt/embeddings/oleObject1.bin"
    assert success["structures"] == [{"id": "s1", "smiles": "CCO"}]
    assert "note" not in success

    empty = result["results"][1]
    assert empty["embedding_path"] == "ppt/embeddings/oleObject2.bin"
    assert empty["structures"] == []
    assert "note" in empty
    assert "no chemistry data recoverable" in empty["note"]

    failure = result["failed"][0]
    assert failure["embedding_path"] == "xl/embeddings/oleObject1.bin"
    assert failure["location"] == {
        "kind": "sheet", "index": None, "sheet_name": "Sheet1", "cell": "F10"}
    assert "simulated ChemDraw conversion failure" in failure["error"]

    # convert_cdx_cdxml was attempted for all three, even after the first
    # two succeeded -- one failure must not abort the batch.
    assert len(bridge.calls) == 3


def test_one_failure_does_not_abort_remaining_batch(monkeypatch):
    """Same as above but with the FAILING embedding first, to make sure
    isolation works regardless of ordering -- a failure early in the loop
    must not prevent later successful embeddings from being processed."""
    entries = [
        {
            "embedding_path": "word/embeddings/oleObject1.bin",
            "cdx_bytes": b"bad-bytes",
            "location": {"kind": "position", "index": 1, "sheet_name": None, "cell": None},
        },
        {
            "embedding_path": "word/embeddings/oleObject2.bin",
            "cdx_bytes": b"good-bytes",
            "location": {"kind": "position", "index": 2, "sheet_name": None, "cell": None},
        },
    ]
    monkeypatch.setattr(office_embed, "find_embedded_cdx", lambda path: entries)
    monkeypatch.setattr(
        cdxml_document, "parse_document",
        lambda text: {"structures": [{"id": "ok"}], "captions": [], "boxes": [],
                       "non_structure_units": [], "violations": {}, "page_bounds": None,
                       "reactions": [], "arrows": [], "brackets": []},
    )

    mcp = _FakeMCP()
    bridge = _FakeBridge(fail_on={0})
    tools = _register(mcp, bridge)
    result = json.loads(tools["chemdraw_extract_office_embeddings"]("fake.docx"))

    assert result["found"] == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["embedding_path"] == "word/embeddings/oleObject1.bin"
    assert len(result["results"]) == 1
    assert result["results"][0]["embedding_path"] == "word/embeddings/oleObject2.bin"
    assert result["results"][0]["structures"] == [{"id": "ok"}]
