"""Structured document-state snapshots (runs on the COM worker thread).
Feeds get_document_state and diff_since_last_check. Distinct from
snapshots.py, which writes file backups for rollback."""
from . import targets


def _human_position(bounds, doc_width, doc_height):
    cx = (bounds["left"] + bounds["right"]) / 2.0
    cy = (bounds["top"] + bounds["bottom"]) / 2.0
    col = "left" if cx < doc_width / 3 else ("center" if cx < 2 * doc_width / 3 else "right")
    row = "top" if cy < doc_height / 3 else ("middle" if cy < 2 * doc_height / 3 else "bottom")
    return f"{row}-{col}" if f"{row}-{col}" != "middle-center" else "center"


def describe_unit(unit, doc_width=540.0, doc_height=720.0):
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


def build_snapshot(doc):
    w = float(doc.Width or 540.0)
    h = float(doc.Height or 720.0)
    return [describe_unit(u, w, h) for u in targets.iter_units(doc)]
