"""Structured document-state snapshots (runs on the COM worker thread).
Feeds get_document_state and diff_since_last_check. Distinct from
snapshots.py, which writes file backups for rollback."""
from collections import Counter

from . import snapshots, targets
from .domain import cdxml_snapshot


def _human_position(bounds, doc_width, doc_height):
    cx = (bounds["left"] + bounds["right"]) / 2.0
    cy = (bounds["top"] + bounds["bottom"]) / 2.0
    col = "left" if cx < doc_width / 3 else ("center" if cx < 2 * doc_width / 3 else "right")
    row = "top" if cy < doc_height / 3 else ("middle" if cy < 2 * doc_height / 3 else "bottom")
    return f"{row}-{col}" if f"{row}-{col}" != "middle-center" else "center"


def describe_unit(unit, doc_width=540.0, doc_height=720.0):
    """Full per-unit COM read: 4 bounds properties + Formula +
    Atoms.Count + Bonds.Count, live. Used directly by callers that only
    need one or a few units (get_layout's non-"document" target), and as
    build_snapshot's fallback for any unit the CDXML-based fast path can't
    safely resolve."""
    oid = targets.ensure_id(unit)
    entry = {"id": oid}
    try:
        objs = targets.unit_objects(unit)
        entry["formula"] = objs.Formula or ""
        entry["atom_count"] = objs.Atoms.Count
        entry["bond_count"] = objs.Bonds.Count
    except Exception:
        entry["formula"] = ""
        entry["atom_count"] = entry["bond_count"] = None
    try:
        bounds = {
            "left": round(unit.Left, 1),
            "top": round(unit.Top, 1),
            "right": round(unit.Right, 1),
            "bottom": round(unit.Bottom, 1),
        }
        entry["bounds"] = bounds
        entry["position"] = _human_position(bounds, doc_width, doc_height)
    except Exception:
        entry["bounds"] = None
        entry["position"] = "unknown"
    return entry


def _fast_entry(uid, info, formula, doc_width, doc_height):
    bounds = info["bounds"]
    if bounds is None:
        return {"id": uid, "formula": formula, "atom_count": info["atom_count"],
                "bond_count": info["bond_count"], "bounds": None,
                "position": "unknown"}
    bounds = {k: round(v, 1) for k, v in bounds.items()}
    return {
        "id": uid, "formula": formula,
        "atom_count": info["atom_count"], "bond_count": info["bond_count"],
        "bounds": bounds, "position": _human_position(bounds, doc_width, doc_height),
    }


def build_snapshot(doc, cache=None):
    """cache: optional per-document cache (see targets.iter_units) so
    repeated snapshots skip the full Groups/Atoms COM rescan when the
    document's unit membership hasn't changed.

    Bounds/atom_count/bond_count come from ONE CDXML export
    (snapshots.export_cdxml_text + domain/cdxml_snapshot.index_units)
    instead of 6 live COM property reads per unit — verified to match COM
    exactly, including for heavily-contracted/nickname structures. Formula
    is NOT derivable from CDXML (a contracted nickname's real formula lives
    in ChemDraw's own nickname database, not the export) so it's still read
    live via targets.unit_formula, but that's cached per (unit, its own
    content signature) and costs one property read per unit rather than
    six.

    iter_units runs BEFORE the CDXML export, not after: iter_units is what
    stamps/retags claude_id tags on units that don't have one yet (or that
    collided with one already claimed this scan), so the export must
    happen after that write for the exported ids to be current.

    A unit falls back to the full per-unit describe_unit read (same as
    before this change) in two cases, both rare and both safe: its id isn't
    present in the CDXML index at all (defensive — shouldn't happen given
    the ordering above), or its id is shared by more than one unit in this
    same call. The latter is a real, documented case (iter_units: "sibling
    fragments can share an inherited tag") — the CDXML index is keyed by
    id, so it can't distinguish two same-id units' individual bounds the
    way a live per-object COM read still can."""
    w = float(doc.Width or 540.0)
    h = float(doc.Height or 720.0)
    units = targets.iter_units(doc, cache)
    ids = [targets.ensure_id(u) for u in units]
    id_counts = Counter(ids)

    cdxml_text = snapshots.export_cdxml_text(doc)
    by_id = cdxml_snapshot.index_units(cdxml_text) if cdxml_text else {}

    out = []
    for unit, uid in zip(units, ids):
        formula = targets.unit_formula(unit, cache)
        info = by_id.get(uid)
        if info is not None and info["bounds"] is not None and id_counts[uid] == 1:
            out.append(_fast_entry(uid, info, formula, w, h))
        else:
            entry = describe_unit(unit, w, h)
            entry["formula"] = formula
            out.append(entry)
    return out
