"""Unit tests for the chemdraw_generate_scope_figure composite tool
(tools/procedure_scope.py) against a fake bridge -- no COM/ChemDraw
involved, only the orchestration logic (label construction, mapping
successful derivatives back to their original substituent index, and
merging the four underlying calls' results into one)."""
import json

import pytest

from tools import procedure_scope


class _FakeMCP:
    """Minimal stand-in for FastMCP: @mcp.tool() just needs to hand back
    the undecorated function so the test can call it directly."""
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeBridge:
    def __init__(self, enum_result, table_result, autonumber_result,
                 warnings_result):
        self._enum_result = enum_result
        self._table_result = table_result
        self._autonumber_result = autonumber_result
        self._warnings_result = warnings_result
        self.calls = {}

    def enumerate_derivatives(self, substituents, scaffold, fmt, properties):
        self.calls["enumerate_derivatives"] = (substituents, scaffold, fmt,
                                                properties)
        return self._enum_result

    def build_scope_table(self, entries, columns, layout, page_width_in):
        self.calls["build_scope_table"] = (entries, columns, layout,
                                            page_width_in)
        return dict(self._table_result)  # fresh copy: with_preview mutates it

    def autonumber(self, target, start, scheme, bold, group_sizes):
        self.calls["autonumber"] = (target, start, scheme, bold, group_sizes)
        return self._autonumber_result

    def check_warnings(self, target):
        self.calls["check_warnings"] = target
        return self._warnings_result


def _register(enum_result, table_result=None, autonumber_result=None,
              warnings_result=None):
    table_result = table_result or {
        "object_ids": ["id-oh", "id-nh2"],
        "fragment_ids": [["id-oh"], ["id-nh2"]],
        "columns": 2,
        "page_width_points": 468.0,
        "violations": {"off_page": []},
        "backup_path": "backup.cdxml",
        "preview_png_base64": None,
    }
    autonumber_result = autonumber_result or {
        "numbered": [{"id": "id-oh", "label": "1"},
                     {"id": "id-nh2", "label": "2"}],
    }
    warnings_result = warnings_result or {
        "total_document_warnings": 0, "flagged": [],
    }
    mcp = _FakeMCP()
    bridge = _FakeBridge(enum_result, table_result, autonumber_result,
                         warnings_result)
    procedure_scope.register(mcp, bridge)
    return mcp.tools["chemdraw_generate_scope_figure"], bridge


_ENUM_RESULT = {
    "scaffold": "c1ccccc1[*]",
    "derivatives": [
        {"substituent": "[*]O", "smiles": "Oc1ccccc1",
         "mw": 94.11, "formula": "C6H6O"},
        {"substituent": "[*]N", "smiles": "Nc1ccccc1",
         "mw": 93.13, "formula": "C6H7N"},
    ],
    "failed": [{"substituent": "bad", "error": "Invalid SMILES"}],
    "count": 2,
}


def test_auto_labels_and_index_mapping_skip_failed_substituent():
    fn, bridge = _register(_ENUM_RESULT)
    # "bad" (index 1) fails enumeration; "[*]O" (0) and "[*]N" (2) succeed.
    result = json.loads(fn(["[*]O", "bad", "[*]N"], scaffold="c1ccccc1[*]"))

    assert len(result["entries"]) == 2
    e0, e1 = result["entries"]
    assert e0["substituent"] == "[*]O" and e0["substituent_index"] == 0
    assert e1["substituent"] == "[*]N" and e1["substituent_index"] == 2
    # auto-label combines running index with formula + MW
    assert e0["label"].startswith("1,")
    assert "C6H6O" in e0["label"] and "94.1" in e0["label"]
    assert e1["label"].startswith("2,")

    assert e0["object_id"] == "id-oh"
    assert e1["object_id"] == "id-nh2"
    assert result["failed_substituents"] == _ENUM_RESULT["failed"]
    assert result["numbered"] == bridge._autonumber_result["numbered"]
    assert result["warnings"] == bridge._warnings_result
    assert "preview_png_base64" not in result  # None -> stripped by with_preview

    # entries passed to build_scope_table used the auto labels, in order
    built_entries = bridge.calls["build_scope_table"][0]
    assert built_entries[0]["representation"] == "Oc1ccccc1"
    assert built_entries[0]["format"] == "smiles"
    assert built_entries[1]["representation"] == "Nc1ccccc1"


def test_explicit_labels_line_up_against_substituents_not_entries():
    fn, bridge = _register(_ENUM_RESULT)
    labels = ["custom-A", "unused-because-bad", "custom-B"]
    result = json.loads(fn(["[*]O", "bad", "[*]N"], scaffold="c1ccccc1[*]",
                           labels=labels))
    e0, e1 = result["entries"]
    assert e0["label"] == "custom-A"
    assert e1["label"] == "custom-B"


def test_labels_length_mismatch_raises():
    fn, _ = _register(_ENUM_RESULT)
    with pytest.raises(ValueError, match="line up positionally"):
        fn(["[*]O", "[*]N"], scaffold="c1ccccc1[*]", labels=["only-one"])


def test_all_failed_short_circuits_before_drawing():
    all_failed = {
        "scaffold": "c1ccccc1[*]",
        "derivatives": [],
        "failed": [{"substituent": "bad", "error": "Invalid SMILES"}],
        "count": 0,
    }
    fn, bridge = _register(all_failed)
    result = json.loads(fn(["bad"], scaffold="c1ccccc1[*]"))
    assert result["entries"] == []
    assert "warning" in result
    assert "build_scope_table" not in bridge.calls
    assert "autonumber" not in bridge.calls
    assert "check_warnings" not in bridge.calls


def test_duplicate_substituent_text_maps_in_first_seen_order():
    dup_enum = {
        "scaffold": "c1ccccc1[*]",
        "derivatives": [
            {"substituent": "[*]O", "smiles": "Oc1ccccc1",
             "mw": 94.11, "formula": "C6H6O"},
            {"substituent": "[*]O", "smiles": "Oc1ccccc1",
             "mw": 94.11, "formula": "C6H6O"},
        ],
        "failed": [],
        "count": 2,
    }
    fn, _ = _register(dup_enum)
    result = json.loads(fn(["[*]O", "[*]O"], scaffold="c1ccccc1[*]"))
    indices = sorted(e["substituent_index"] for e in result["entries"])
    assert indices == [0, 1]


def test_full_chain_called_with_expected_args():
    fn, bridge = _register(_ENUM_RESULT)
    fn(["[*]O", "bad", "[*]N"], scaffold="c1ccccc1[*]",
       properties=["mw", "formula"], columns=2, layout="single-column",
       autonumber_target="document", autonumber_start=1,
       autonumber_scheme="numeric", check_warnings_target="document")

    enum_args = bridge.calls["enumerate_derivatives"]
    assert enum_args[0] == ["[*]O", "bad", "[*]N"]
    assert enum_args[1] == "c1ccccc1[*]"
    assert enum_args[2] == "smiles"
    assert enum_args[3] == ["mw", "formula"]

    assert bridge.calls["autonumber"][0] == "document"
    assert bridge.calls["check_warnings"] == "document"
