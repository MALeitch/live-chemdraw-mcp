"""Shared driver for budgeted, resumable per-unit batch operations
(issue #27).

Grew out of #24: `chemdraw_transform(action="clean")` on a whole-document
target used to hand ChemDraw one giant IChemDrawObjects collection, which
doesn't scale (a 24-structure page burned 785s of CPU and never returned).
The fix was per-unit iteration (`prototypes/clean_batch.py`, then
`bridge._manipulation.transform`'s own per-unit path) -- but a per-unit
loop over a genuinely large page (hundreds to thousands of structures, see
#28/#29) still has no wall-clock budget and no way to resume: the whole
call either finishes inside one `SLOW_TIMEOUT` or times out with nothing
usable returned, discarding whatever units it already finished.

This module is pure Python (zero COM) -- it just drives an arbitrary
per-item callable with budget/slice/failure-isolation bookkeeping. The
COM-touching work stays entirely in the caller's `work_fn`.
"""
import time


def run_batch(items, work_fn, id_fn, start=0, limit=None, budget=None):
    """Process items[start:start+limit] (or items[start:] if limit is
    None) by calling work_fn(item) once per item, isolating a per-item
    failure into `failed` (matching the edit_atoms/edit_bonds/remove
    convention already used elsewhere in this codebase) rather than
    aborting the whole batch.

    id_fn(item) -> a stable identifier for reporting (typically
    targets.ensure_id(unit)) -- kept separate from work_fn so callers that
    only need the id for error reporting don't have to re-derive it
    inside their own work_fn.

    budget: wall-clock seconds. Checked BEFORE each item (never mid-item
    -- a single unit's own COM call is never interrupted, matching every
    other timeout in this codebase: the worker's own SLOW_TIMEOUT can't
    interrupt an in-flight call either, see com/worker.py). `resume_at`
    covers BOTH ways there can be more work left: the budget ran out
    partway through the slice, or `limit` itself didn't reach the end of
    `items`. It's the single field a caller loops on: call once with
    start=0, then `while result["resume_at"] is not None: call again with
    start=result["resume_at"]`. None means every item in `items` (not
    just this call's slice) has now been attempted, across however many
    calls it took.

    Returns: {"succeeded": [ids...], "failed": [{"id","error"}...],
    "resume_at": int|None, "processed": int, "total": int,
    "elapsed": float, "median_seconds": float|None,
    "slowest": [{"id","seconds"}, ...5]}. `processed`/`total` describe
    THIS call's slice; `total` is len(items) so a caller can judge overall
    progress. The timing fields are the same "which structure is actually
    slow" diagnostic prototypes/clean_batch.py's own report was built
    around -- turns "this is slow" into "structure #13 is slow, the other
    23 average 0.01s", which is what actually tells a caller whether an
    approach is viable at the page's real scale."""
    total = len(items)
    hi = total if limit is None else min(total, start + limit)
    work = items[start:hi]

    succeeded, failed = [], []
    timings = []  # (seconds, id)
    t_start = time.time()
    budget_stopped_at = None

    for offset, item in enumerate(work):
        i = start + offset
        if budget is not None and (time.time() - t_start) > budget:
            budget_stopped_at = i
            break
        item_id = id_fn(item)
        t0 = time.time()
        try:
            work_fn(item)
            succeeded.append(item_id)
        except Exception as exc:
            failed.append({"id": item_id, "error": str(exc)})
        timings.append((time.time() - t0, item_id))

    resume_at = budget_stopped_at if budget_stopped_at is not None else (hi if hi < total else None)

    timings.sort(reverse=True)
    median_seconds = None
    if timings:
        secs = sorted(s for s, _ in timings)
        median_seconds = secs[len(secs) // 2]

    return {
        "succeeded": succeeded,
        "failed": failed,
        "resume_at": resume_at,
        "processed": len(succeeded) + len(failed),
        "total": total,
        "elapsed": time.time() - t_start,
        "median_seconds": median_seconds,
        "slowest": [{"id": i, "seconds": round(s, 3)} for s, i in timings[:5]],
    }
