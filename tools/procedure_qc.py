"""One-call publication-readiness QC.

Bundles the checks a careful chemist runs right before finalizing/exporting
a scheme, so verifying finished work is a single named tool call instead of
a judgment call about which of several separate checkers to run (and
whether to bother): chemical sanity (chemdraw_check_warnings), duplicate
structures (chemdraw_find_duplicates), and layout problems (the
`violations` slice of chemdraw_describe_canvas).
"""
from ._common import TARGET_DOC, as_json
from .structure import _parse


def register(mcp, bridge):
    @mcp.tool(description=(
        "Is this page publication-ready? ONE call that bundles the three "
        "checks worth running right before finalizing/exporting a scheme: "
        "chemical sanity (chemdraw_check_warnings — valence errors etc.), "
        "duplicate structures (chemdraw_find_duplicates — 'exact' true "
        "duplicates and 'skeleton' stereo/tautomer variants), and layout "
        "problems (just the `violations` slice of chemdraw_describe_canvas "
        "— overlapping structures, structures overflowing their panel box, "
        "structures/captions off the real page — not the full structure/"
        "caption/box inventory, since this tool is meant to be cheap and "
        "QC-only; call chemdraw_describe_canvas separately if you need the "
        "full picture). Always runs whole-document: this is a final "
        "page-wide gate, not a scoped probe, so there is no region/target "
        "passthrough for the layout half — target only scopes the "
        "chemical-warning and duplicate checks (default 'document'; narrow "
        "it if you only care about a subset of structures, though 'ready' "
        "still reflects layout violations for the WHOLE page regardless). "
        "Returns one merged report: chemical_warnings, duplicate_groups, "
        "layout_violations, and `ready` — true only when there are zero "
        "chemical warnings, zero duplicate groups (exact or skeleton), and "
        "every layout violations list is empty. `ready: false` means do "
        "NOT finalize/export this scheme yet — go look at the specific "
        "offending field (chemical_warnings.flagged for the bad atom/bond, "
        "duplicate_groups.exact/skeleton for which structures collide, "
        "layout_violations.overlapping_structures/overflowing_box/off_page "
        "for what to move) rather than re-running every individual checker "
        "yourself. " + TARGET_DOC))
    def chemdraw_finalize_check(target: str = "document") -> str:
        parsed = _parse(target)
        warnings = bridge.check_warnings(parsed)
        duplicates = bridge.find_duplicates(parsed)
        canvas = bridge.describe_canvas(None)
        violations = canvas.get("violations", {})

        has_warnings = bool(warnings.get("flagged"))
        has_duplicates = bool(duplicates.get("exact")) or bool(
            duplicates.get("skeleton"))
        has_violations = any(bool(v) for v in violations.values())

        return as_json({
            "chemical_warnings": warnings,
            "duplicate_groups": duplicates,
            "layout_violations": violations,
            "ready": not (has_warnings or has_duplicates or has_violations),
        })
