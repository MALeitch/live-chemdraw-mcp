"""Lightweight test of the chemdraw_finalize_check tool wrapper.

No COM involved: `bridge` is a stub exposing just the three methods
procedure_qc.register() calls, with canned return shapes matching the real
bridge methods (chemdraw_connector/bridge/_properties_qc.py's
check_warnings/find_duplicates and _layout.py's describe_canvas). `mcp` is a
stub whose .tool() decorator just captures the wrapped function so it can be
called directly, mirroring how tools/*.py modules get registered onto a
real FastMCP instance.
"""
import json

from tools import procedure_qc


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeBridge:
    def __init__(self, warnings=None, duplicates=None, violations=None):
        self._warnings = warnings or {"total_document_warnings": 0, "flagged": []}
        self._duplicates = duplicates or {"exact": [], "skeleton": []}
        self._violations = violations or {
            "overlapping_structures": [], "overflowing_box": [], "off_page": []}
        self.seen_targets = []

    def check_warnings(self, target):
        self.seen_targets.append(("check_warnings", target))
        return self._warnings

    def find_duplicates(self, target):
        self.seen_targets.append(("find_duplicates", target))
        return self._duplicates

    def describe_canvas(self, region):
        self.seen_targets.append(("describe_canvas", region))
        return {
            "structures": ["would be stripped by the tool"],
            "captions": [],
            "boxes": [],
            "violations": self._violations,
        }


def _register_and_call(bridge, target=None):
    mcp = _FakeMCP()
    procedure_qc.register(mcp, bridge)
    fn = mcp.tools["chemdraw_finalize_check"]
    result = json.loads(fn() if target is None else fn(target))
    return result


def test_clean_document_is_ready():
    result = _register_and_call(_FakeBridge())
    assert result["ready"] is True
    assert result["chemical_warnings"]["flagged"] == []
    assert result["duplicate_groups"] == {"exact": [], "skeleton": []}
    assert result["layout_violations"] == {
        "overlapping_structures": [], "overflowing_box": [], "off_page": []}
    # describe_canvas's full structure/caption/box inventory must NOT leak
    # into the merged report -- only its violations sub-object.
    assert "structures" not in result
    assert "captions" not in result
    assert "boxes" not in result


def test_chemical_warning_blocks_ready():
    bridge = _FakeBridge(warnings={
        "total_document_warnings": 1,
        "flagged": [{"kind": "atom", "index": 1, "warning": "bad valence"}],
    })
    result = _register_and_call(bridge)
    assert result["ready"] is False


def test_duplicate_group_blocks_ready():
    bridge = _FakeBridge(duplicates={"exact": [["a", "b"]], "skeleton": []})
    result = _register_and_call(bridge)
    assert result["ready"] is False


def test_layout_violation_blocks_ready():
    bridge = _FakeBridge(violations={
        "overlapping_structures": [], "overflowing_box": [],
        "off_page": [{"id": "claude-1", "edges": ["right"]}]})
    result = _register_and_call(bridge)
    assert result["ready"] is False


def test_target_passed_through_to_warnings_and_duplicates_only():
    bridge = _FakeBridge()
    _register_and_call(bridge, target="selection")
    kinds = dict(bridge.seen_targets)
    assert kinds["check_warnings"] == "selection"
    assert kinds["find_duplicates"] == "selection"
    # describe_canvas has no id-based target concept -- always whole-document.
    assert kinds["describe_canvas"] is None
