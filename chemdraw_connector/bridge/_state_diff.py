"""Full-document state snapshots and before/after diffing."""
from .. import state
from ..domain import canvas, diff
from ._plumbing import SLOW_TIMEOUT


class _StateDiff:
    def get_document_state(self):
        def go():
            doc = self._doc()
            snap = state.build_snapshot(doc, self._cache_for(doc))
            self._last_snapshot = snap  # raw, unfiltered — diff baseline unchanged
            cap_entries, _ = self._gather_captions(doc)
            boxes = self._graphics_boxes(doc)
            out = canvas.build_canvas(snap, cap_entries, boxes)
            return {
                "document": doc.name,
                "structures": out["structures"],
                "non_structure_units": out["non_structure_units"],
                "overlapping_pairs": out["violations"]["overlapping_structures"],
                "chemical_warnings": doc.NumChemicalWarnings,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def diff_since_last_check(self):
        def go():
            doc = self._doc()
            before = self._last_snapshot
            after = state.build_snapshot(doc, self._cache_for(doc))
            self._last_snapshot = after
            if before is None:
                return {
                    "note": "No earlier snapshot existed; this call established the baseline.",
                    "structures": after,
                }
            return diff.diff_snapshots(before, after)
        return self._run(go, timeout=SLOW_TIMEOUT)
