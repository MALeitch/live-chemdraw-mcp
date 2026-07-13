"""Pure comparison of two document-state snapshots.

A snapshot is a list of dicts with at least: id, formula, bounds
(dict with left/top/right/bottom), atom_count, bond_count.
"""

MOVE_TOLERANCE_PT = 2.0


def _center(bounds):
    return (
        (bounds["left"] + bounds["right"]) / 2.0,
        (bounds["top"] + bounds["bottom"]) / 2.0,
    )


def diff_snapshots(before, after):
    before_by_id = {o["id"]: o for o in before}
    after_by_id = {o["id"]: o for o in after}

    added = [o for oid, o in after_by_id.items() if oid not in before_by_id]
    removed = [o for oid, o in before_by_id.items() if oid not in after_by_id]
    moved, modified = [], []

    for oid, new in after_by_id.items():
        old = before_by_id.get(oid)
        if old is None:
            continue
        changed = {}
        for field in ("formula", "atom_count", "bond_count"):
            if old.get(field) != new.get(field):
                changed[field] = {"from": old.get(field), "to": new.get(field)}
        if changed:
            modified.append({"id": oid, "changes": changed})
        if old.get("bounds") and new.get("bounds"):
            (ox, oy), (nx, ny) = _center(old["bounds"]), _center(new["bounds"])
            if abs(nx - ox) > MOVE_TOLERANCE_PT or abs(ny - oy) > MOVE_TOLERANCE_PT:
                moved.append({
                    "id": oid,
                    "dx_points": round(nx - ox, 1),
                    "dy_points": round(ny - oy, 1),
                })

    return {
        "added": added,
        "removed": removed,
        "moved": moved,
        "modified": modified,
        "unchanged_count": len(after_by_id)
        - len(added)
        - len({m["id"] for m in moved} | {m["id"] for m in modified}),
    }
