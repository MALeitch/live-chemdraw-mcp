"""make_reaction_route's step isolation -- pure, no COM.

Regression test. CONFIRMED BY STATIC REASONING that the
per-item try used to wrap BOTH the mutation (make_reaction_scheme) and the
follow-up QC call (check_warnings) -- so a step that drew successfully but
whose check_warnings call happened to raise was reported in `failed`
anyway, discarding its object_ids/backup_path and telling the caller the
step never happened when the canvas disagreed. This is the mirror-image
of the per-item-isolation bug pattern the other batch tools were checked
against (an except covering MORE than the mutation, not less).

make_reaction_route composes self.make_reaction_scheme/check_warnings/
find_duplicates directly (not wrapped in self._run), so this tests the
orchestration logic by overriding just those three on a minimal subclass
-- no COM/win32 mocking needed.
"""
from chemdraw_connector.bridge import _reaction as rxn


class _StubReactionBridge(rxn._Reaction):
    def __init__(self):
        self.scheme_calls = []
        self.warnings_calls = []
        self.scheme_should_fail_for = set()   # step indices
        self.warnings_should_fail_for = set()  # step indices

    def make_reaction_scheme(self, reactants, products, reagents_text=None,
                             fmt="smiles", conditions_text=None,
                             anchor_y=None, yields=None):
        i = len(self.scheme_calls)
        self.scheme_calls.append(i)
        if i in self.scheme_should_fail_for:
            raise ValueError(f"bad step {i}")
        return {
            "object_ids": [f"claude-step{i}"],
            "arrow_native": True,
            "arrow_object_id": f"claude-arrow{i}",
            "violations": {"overlapping": [], "mislaid_captions": [], "off_page": []},
            "backup_path": f"backup-{i}.cdxml",
            "preview_png_base64": None,
        }

    def check_warnings(self, object_ids):
        self.warnings_calls.append(object_ids)
        # Keyed by which step's object_ids these are (not call order --
        # a step whose OWN make_reaction_scheme failed never reaches
        # check_warnings at all, so call order and step index diverge
        # whenever an earlier step fails to draw).
        step_idx = int(object_ids[0].replace("claude-step", ""))
        if step_idx in self.warnings_should_fail_for:
            raise RuntimeError("ChemDraw warning check timed out")
        return {"flagged": []}

    def find_duplicates(self, target="document"):
        return {"exact": [], "skeleton": []}


def test_step_with_failing_qc_still_reported_as_drawn_not_failed():
    bridge = _StubReactionBridge()
    bridge.warnings_should_fail_for = {0}

    result = bridge.make_reaction_route([
        {"reactants": ["CC(=O)Cl"], "products": ["CC(=O)O"]},
    ])

    assert result["failed"] == []  # NOT demoted to failed
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["step_index"] == 0
    assert step["object_ids"] == ["claude-step0"]  # not lost
    assert step["backup_path"] == "backup-0.cdxml"  # not lost
    assert step["warnings"] is None
    assert "ChemDraw warning check timed out" in step["warnings_error"]


def test_step_that_fails_to_draw_is_still_reported_failed():
    bridge = _StubReactionBridge()
    bridge.scheme_should_fail_for = {0}

    result = bridge.make_reaction_route([
        {"reactants": ["not smiles"], "products": ["also not smiles"]},
    ])

    assert result["steps"] == []
    assert len(result["failed"]) == 1
    assert result["failed"][0]["step_index"] == 0
    assert "bad step 0" in result["failed"][0]["error"]
    # A step that never drew must not have triggered a warnings check.
    assert bridge.warnings_calls == []


def test_mixed_route_draw_failure_qc_failure_and_success_all_isolated():
    bridge = _StubReactionBridge()
    bridge.scheme_should_fail_for = {1}       # step 1 never draws
    bridge.warnings_should_fail_for = {2}     # step 2 draws but QC fails

    result = bridge.make_reaction_route([
        {"reactants": ["CC(=O)Cl"], "products": ["CC(=O)O"]},   # step 0: clean
        {"reactants": ["bad"], "products": ["bad"]},              # step 1: draw fails
        {"reactants": ["c1ccccc1"], "products": ["c1ccccc1O"]},   # step 2: QC fails
    ])

    assert [f["step_index"] for f in result["failed"]] == [1]
    step_indices = [s["step_index"] for s in result["steps"]]
    assert step_indices == [0, 2]  # step 1 excluded, 0 and 2 both present

    clean = next(s for s in result["steps"] if s["step_index"] == 0)
    assert clean["warnings"] == {"flagged": []}
    assert "warnings_error" not in clean

    qc_failed = next(s for s in result["steps"] if s["step_index"] == 2)
    assert qc_failed["warnings"] is None
    assert "warnings_error" in qc_failed
    assert qc_failed["object_ids"] == ["claude-step2"]  # step still drawn, id preserved
