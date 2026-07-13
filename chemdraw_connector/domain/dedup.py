"""Duplicate detection by InChIKey."""
from collections import defaultdict


def find_duplicate_groups(keyed_items):
    """keyed_items: list of (object_id, inchikey). Returns dict with 'exact'
    (same full InChIKey) and 'skeleton' (same connectivity block but different
    full key — usually stereo/tautomer variants of the same skeleton)."""
    by_full = defaultdict(list)
    by_skeleton = defaultdict(list)
    for oid, key in keyed_items:
        if not key:
            continue
        key = key.strip()
        by_full[key].append(oid)
        by_skeleton[key.split("-")[0]].append(oid)

    exact = [ids for ids in by_full.values() if len(ids) > 1]
    exact_ids = {i for grp in exact for i in grp}
    skeleton = []
    for ids in by_skeleton.values():
        if len(ids) > 1 and not set(ids) <= exact_ids:
            skeleton.append(ids)
    return {"exact": exact, "skeleton": skeleton}
