"""ChemDrawBridge: every tool call funnels through here.

Public methods are thread-safe: each builds a closure and submits it to the
single COM worker thread. Only plain Python data (str/int/dict/bytes) crosses
the boundary — COM objects never leave the worker thread.
"""
import base64
import csv
import os

import pythoncom
import pywintypes

from . import snapshots, state, targets
from .com import nudge
from .com import types as t
from .com.connection import Connection, is_dead_proxy
from .com.worker import ComWorker, DEFAULT_TIMEOUT
from .domain import (characterization, cdxml_graph, dedup, diff, enumeration,
                     label_filter, layout_math, numbering, substructures)
from .domain.style_presets import PRESETS, get_preset
from .errors import ChemDrawError, InvalidInputError

SLOW_TIMEOUT = 45.0  # name<->structure conversions and batch ops can be slow
# expand_labels intentionally makes ONE COM call covering every matching
# label at once (verified safe — unlike contraction, ExpandLabelsToStructure
# doesn't merge separate structures, see bridge docstring on expand_labels).
# A single call doing genuinely more total ChemDraw-side work needs more
# runway than SLOW_TIMEOUT before the wedge detector should worry.
BULK_TIMEOUT = 180.0


class _StaleHandleMap(Exception):
    """A cached COM proxy or doc-unique atom/bond id from an earlier
    _build_handle_map call turned out unusable for a later match in the same
    round — either a genuinely dead COM proxy, or the id space shifted
    underneath it. Caller rebuilds the map fresh and retries once. Internal
    to contract_functional_groups; never escapes to a tool caller."""


def _com_text(data):
    if isinstance(data, memoryview):
        return data.tobytes().decode("utf-8", "replace")
    return "" if data is None else str(data)


def _com_bytes(data):
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return _com_text(data).encode("utf-8")


class ChemDrawBridge:
    def __init__(self):
        self._conn = Connection()
        self._worker = ComWorker(unblock_hook=self._nudge_chemdraw)
        self._last_snapshot = None  # for diff_since_last_check
        self._doc_name = None  # tracked working document (see _doc)
        self._caches = {}  # doc.name -> targets.py unit/atom/bond cache
        self._last_backup = {}  # doc.name -> {"sig": ..., "path": ...}

    # ---------- plumbing ----------

    def _cache_for(self, doc):
        """Per-document cache passed to targets.iter_units/resolve/
        find_by_id/unit_atoms_bonds so repeated calls skip full-document
        COM rescans when nothing has changed (see targets.doc_signature).
        Keyed by doc.name, the same document-identity key _doc() itself
        tracks, so switching the active document can never serve one
        document's cached units for another."""
        return self._caches.setdefault(doc.name, {})

    def _maybe_snapshot(self, doc):
        """Backup-on-mutation, debounced: reuse the last backup's path when
        doc_signature(doc) hasn't changed since it was taken (same
        signature check as the unit/atom/bond cache), instead of
        re-exporting + rewriting a fresh CDXML file on every mutating call
        in a rapid chain. When a fresh backup IS needed, the COM export
        happens here (must run on the worker thread) but the disk write is
        handed off (see snapshots.write_backup_file(wait=False)) so a slow
        disk never blocks the worker thread. Worker thread only."""
        sig = targets.doc_signature(doc)
        last = self._last_backup.get(doc.name)
        if last is not None and last["sig"] == sig:
            return last["path"]
        text = snapshots.export_cdxml_text(doc)
        if text is None:
            return None
        path = snapshots.write_backup_file(doc.name, text, wait=False)
        self._last_backup[doc.name] = {"sig": sig, "path": path}
        return path

    def _nudge_chemdraw(self):
        """Break ChemDraw out of an interactive block (in-canvas text edit).
        Runs off the COM worker thread via pure Win32 — see com/nudge.py."""
        return bool(nudge.nudge_escape(self._conn.hwnd))

    def _run(self, fn, timeout=DEFAULT_TIMEOUT):
        def wrapped():
            try:
                return fn()
            except pywintypes.com_error as exc:
                if is_dead_proxy(exc):
                    # ChemDraw died mid-call. Reconnect for future calls, but
                    # report honestly — a silent retry against a fresh instance
                    # would masquerade as "object not found."
                    try:
                        self._conn.app()
                    except Exception:
                        pass
                    raise ChemDrawError(
                        "ChemDraw stopped responding during this operation (it "
                        "may have crashed) and a fresh instance was reconnected. "
                        "Check the ChemDraw window — unsaved work from before "
                        "the crash may be recoverable from the automatic "
                        f"backups in {snapshots.BACKUP_DIR}"
                    ) from exc
                raise ChemDrawError(self._explain(exc)) from exc
        return self._worker.submit(wrapped, timeout=timeout)

    @staticmethod
    def _explain(exc):
        parts = [str(p) for p in (exc.excepinfo or []) if isinstance(p, str) and p]
        detail = "; ".join(parts) or exc.strerror or str(exc)
        return f"ChemDraw rejected the operation: {detail}"

    def _doc(self):
        """The working document. Worker thread only.

        Quirk (probed live): ActiveDocument intermittently returns None over
        COM — even mid-session for documents that were being used moments
        before. So: trust ActiveDocument when it answers (it tracks the user's
        real focus), but remember the working document's name and fall back to
        re-resolving that name when ActiveDocument goes blank, instead of
        grabbing an arbitrary document or spawning new ones.
        """
        app = self._conn.app()
        doc = app.ActiveDocument
        if doc is not None:
            self._doc_name = doc.name
            return doc
        if self._doc_name:
            for i in range(1, app.Documents.Count + 1):
                cand = app.Documents.Item(i)
                if cand.name == self._doc_name:
                    return cand
            self._doc_name = None  # tracked document was closed
        if app.Documents.Count > 0:
            doc = app.Documents.Item(app.Documents.Count)
        else:
            doc = app.Documents.Add()
        doc.Activate()
        self._doc_name = doc.name
        return doc

    @staticmethod
    def _insert_raw(objs, mime, payload):
        raw = objs._oleobj_
        dispid = raw.GetIDsOfNames(0, "Data")
        raw.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, mime, payload)

    @staticmethod
    def _set_position(obj, x, y):
        """Move a positional object (caption, arrow, atom) to x, y in points."""
        try:
            pos = obj.Position
            pos.X = x
            pos.Y = y
            obj.Position = pos
            return True
        except Exception:
            return False

    def _validate_input(self, representation, fmt):
        """RDKit pre-validation for formats it can parse; canonical output.
        Formats RDKit can't judge (name, cdxml, helm...) pass through."""
        if fmt == "smiles":
            return enumeration.validate_smiles(representation)
        if fmt in ("molfile", "molfile-v3000"):
            enumeration.validate_molblock(representation)
        return representation

    def _insert_structure_units(self, doc, representation, fmt):
        """Insert and return the new units (worker thread). Verifies atoms
        actually appeared — an unknown/silently-ignored payload is an error,
        not a success."""
        mime = t.mime_for(fmt)
        atoms_before = doc.Atoms.Count
        groups_before = doc.Groups.Count
        self._insert_raw(doc.Objects, mime, representation)
        if doc.Atoms.Count == atoms_before:
            raise InvalidInputError(
                f"ChemDraw did not insert anything for the given {fmt} input "
                f"({representation[:80]!r}). Check the input is valid."
            )
        new_units = [
            doc.Groups.Item(i)
            for i in range(groups_before + 1, doc.Groups.Count + 1)
        ]
        return new_units

    def _describe_units(self, doc, units):
        w, h = float(doc.Width or 540), float(doc.Height or 720)
        return [state.describe_unit(u, w, h) for u in units]

    # ---------- document/session ----------

    def status(self):
        def go():
            info = self._conn.info()
            app = self._conn.app()
            doc = app.ActiveDocument
            info["active_document"] = doc.name if doc is not None else None
            info["open_documents"] = app.Documents.Count
            if doc is not None:
                info["structures_on_page"] = len(targets.iter_units(doc, self._cache_for(doc)))
                info["chemical_warnings"] = doc.NumChemicalWarnings
            return info
        return self._run(go)

    def new_document(self):
        def go():
            doc = self._conn.app().Documents.Add()
            doc.Activate()
            return {"active_document": doc.name}
        return self._run(go)

    # One reusable scratch document. Document.Close() is a no-op over COM
    # (probed live on ChemDraw 26 — every variant), so every throwaway
    # document becomes a permanent window until the user closes it by hand.
    # All temporary/test work therefore shares this single document, cleared
    # on each acquisition.
    SCRATCH_DOC_NAME = "chemdraw-mcp-scratch.cdxml"

    def use_scratch_document(self):
        def go():
            app = self._conn.app()
            for i in range(1, app.Documents.Count + 1):
                doc = app.Documents.Item(i)
                if doc.name == self.SCRATCH_DOC_NAME:
                    doc.Activate()
                    cleared = doc.Atoms.Count
                    doc.Objects.Clear()
                    self._doc_name = doc.name
                    return {"active_document": doc.name, "reused": True,
                            "cleared_atoms": cleared}
            doc = app.Documents.Add()
            doc.Activate()
            entry = {"reused": False, "cleared_atoms": 0}
            try:
                path = os.path.join(os.path.dirname(snapshots.BACKUP_DIR),
                                    self.SCRATCH_DOC_NAME)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                doc.SaveAs(path)
            except Exception as exc:
                # Still usable, just not findable by name next time.
                entry["note"] = (f"scratch document could not be saved under "
                                 f"its stable name: {exc}")
            self._doc_name = doc.name
            entry["active_document"] = doc.name
            return entry
        return self._run(go, timeout=SLOW_TIMEOUT)

    def open_document(self, path):
        def go():
            doc = self._conn.app().Documents.Open(path)
            doc.Activate()
            return {"active_document": doc.name, "path": path}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def save_document(self, path=None):
        def go():
            doc = self._doc()
            if path:
                doc.SaveAs(path)
            else:
                doc.Save()
            return {"saved": True, "path": path or doc.FullName}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def undo(self):
        return self._run(lambda: (self._doc().Undo(), {"ok": True})[1])

    def redo(self):
        return self._run(lambda: (self._doc().Redo(), {"ok": True})[1])

    def list_documents(self):
        def go():
            app = self._conn.app()
            active = app.ActiveDocument
            return {
                "documents": [
                    app.Documents.Item(i).name
                    for i in range(1, app.Documents.Count + 1)
                ],
                "active": active.name if active is not None else None,
            }
        return self._run(go)

    def set_active_document(self, name):
        def go():
            app = self._conn.app()
            for i in range(1, app.Documents.Count + 1):
                doc = app.Documents.Item(i)
                if doc.name == name:
                    doc.Activate()
                    return {"active_document": doc.name}
            raise ChemDrawError(
                f"No open document named {name!r}. Open documents: "
                f"{[app.Documents.Item(i).name for i in range(1, app.Documents.Count + 1)]}"
            )
        return self._run(go)

    # ---------- selection ----------

    def get_selection(self):
        def go():
            doc = self._doc()
            units = targets.resolve(doc, "selection", self._cache_for(doc))
            return {"selected": self._describe_units(doc, units)}
        return self._run(go)

    def select(self, object_id):
        def go():
            doc = self._doc()
            unit = targets.find_by_id(doc, object_id, self._cache_for(doc))
            targets.unit_objects(unit).Select()
            return {"selected": object_id}
        return self._run(go)

    # ---------- state & diff ----------

    def get_document_state(self):
        def go():
            doc = self._doc()
            snap = state.build_snapshot(doc)
            boxes, ids = [], []
            for entry in snap:
                if entry.get("bounds"):
                    b = entry["bounds"]
                    boxes.append(layout_math.Box(b["left"], b["top"], b["right"], b["bottom"]))
                    ids.append(entry["id"])
            overlaps = layout_math.find_overlaps(boxes, ids)
            self._last_snapshot = snap
            return {
                "document": doc.name,
                "structures": snap,
                "overlapping_pairs": overlaps,
                "chemical_warnings": doc.NumChemicalWarnings,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def diff_since_last_check(self):
        def go():
            doc = self._doc()
            before = self._last_snapshot
            after = state.build_snapshot(doc)
            self._last_snapshot = after
            if before is None:
                return {
                    "note": "No earlier snapshot existed; this call established the baseline.",
                    "structures": after,
                }
            return diff.diff_snapshots(before, after)
        return self._run(go, timeout=SLOW_TIMEOUT)

    # ---------- structure I/O ----------

    def insert_structure(self, representation, fmt="smiles", position=None):
        representation = self._validate_input(representation, fmt)

        def go():
            doc = self._doc()
            units = self._insert_structure_units(doc, representation, fmt)
            if position and units:
                x, y = position
                for u in units:
                    objs = targets.unit_objects(u)
                    cx = (u.Left + u.Right) / 2.0
                    cy = (u.Top + u.Bottom) / 2.0
                    objs.Move(x - cx, y - cy)
            return {"inserted": self._describe_units(doc, units)}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def export_structure(self, fmt="molfile", target="selection"):
        def go():
            doc = self._doc()
            mime = t.mime_for(fmt)
            units = targets.resolve(doc, target, self._cache_for(doc))
            out = []
            for u in units:
                data = targets.unit_objects(u).GetData(mime)
                out.append({
                    "id": targets.ensure_id(u),
                    "data": _com_text(data),
                })
            return {"format": fmt, "structures": out}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def export_image(self, fmt="png", target="selection", path=None, dpi=300):
        def go():
            doc = self._doc()
            mime = t.mime_for(fmt)
            if target == "document":
                objs = doc.Objects
            else:
                units = targets.resolve(doc, target, self._cache_for(doc))
                if len(units) == 1:
                    objs = targets.unit_objects(units[0])
                else:
                    for u in units:
                        targets.unit_objects(u).Select()
                    objs = doc.Selection.Objects
            data = _com_bytes(objs.GetData(mime, dpi))
            if not data:
                raise ChemDrawError(f"ChemDraw returned no {fmt} data")
            if path:
                with open(path, "wb") as fh:
                    fh.write(data)
                return {"path": path, "bytes": len(data)}
            return {"format": fmt, "base64": base64.b64encode(data).decode()}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def _preview_png(self, doc):
        """Small whole-document preview for visual review; worker thread."""
        try:
            data = _com_bytes(doc.Objects.GetData("image/png", 96))
            return base64.b64encode(data).decode() if data else None
        except Exception:
            return None

    def copy_to_clipboard(self, target="selection"):
        def go():
            doc = self._doc()
            units = targets.resolve(doc, target, self._cache_for(doc))
            for u in units:
                targets.unit_objects(u).Select()
            doc.Selection.Objects.Copy()
            return {"copied": [targets.ensure_id(u) for u in units]}
        return self._run(go)

    # ---------- properties / analysis / QC ----------

    def get_properties(self, target="selection"):
        def go():
            doc = self._doc()
            out = []
            for u in targets.resolve(doc, target, self._cache_for(doc)):
                objs = targets.unit_objects(u)
                out.append({
                    "id": targets.ensure_id(u),
                    "formula": objs.Formula or "",
                    "molecular_weight": round(objs.MolecularWeight, 4),
                    "exact_mass": round(objs.ExactMass, 4),
                })
            return {"properties": out}
        return self._run(go)

    def get_iupac_name(self, target="selection", html=False):
        fmt = "htmlname" if html else "name"

        def go():
            doc = self._doc()
            out = []
            for u in targets.resolve(doc, target, self._cache_for(doc)):
                data = targets.unit_objects(u).GetData(t.mime_for(fmt))
                out.append({"id": targets.ensure_id(u), "name": _com_text(data)})
            return {"names": out}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def generate_characterization_text(self, target="selection",
                                       ion_mode="[M+H]+", technique="ESI"):
        props = self.get_properties(target)["properties"]
        lines = [
            {
                "id": p["id"],
                "formula": p["formula"],
                "text": characterization.hrms_line(
                    p["formula"], p["exact_mass"], ion_mode, technique
                ),
            }
            for p in props
        ]
        return {"characterization": lines}

    def find_duplicates(self, target="document"):
        exported = self.export_structure("inchikey", target)["structures"]
        keyed = [(e["id"], e["data"]) for e in exported]
        return dedup.find_duplicate_groups(keyed)

    def check_warnings(self, target="document"):
        def go():
            doc = self._doc()
            warnings = []
            if target == "document":
                collections = [
                    ([doc.Atoms.Item(i) for i in range(1, doc.Atoms.Count + 1)], "atom"),
                    ([doc.Bonds.Item(i) for i in range(1, doc.Bonds.Count + 1)], "bond"),
                ]
            else:
                collections = []
                for u in targets.resolve(doc, target, self._cache_for(doc)):
                    atoms, bonds = targets.unit_atoms_bonds(doc, u, self._cache_for(doc))
                    collections.extend([(atoms, "atom"), (bonds, "bond")])
            for coll, kind in collections:
                for i, item in enumerate(coll, start=1):
                    msg = item.ChemicalWarning
                    if msg:
                        entry = {"kind": kind, "index": i, "warning": msg}
                        if kind == "atom":
                            entry["element"] = t.element_symbol(item.ElementNumber)
                        warnings.append(entry)
            return {
                "total_document_warnings": doc.NumChemicalWarnings,
                "flagged": warnings,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    # ---------- manipulation ----------

    def transform(self, target="selection", action="clean", dx=0.0, dy=0.0,
                  degrees=0.0, factor=1.0, vertical=False):
        def go():
            doc = self._doc()
            units = targets.resolve(doc, target, self._cache_for(doc))
            for u in units:
                objs = targets.unit_objects(u)
                if action == "move":
                    objs.Move(dx, dy)
                elif action == "rotate":
                    objs.Rotate(degrees, True)
                elif action == "scale":
                    objs.Scale(factor, True, True)
                elif action == "flip":
                    objs.Flip(vertical, False)
                elif action == "clean":
                    objs.Clean(False)
                else:
                    raise ValueError(
                        f"Unknown action {action!r}; expected move/rotate/scale/flip/clean"
                    )
            return {"transformed": [targets.ensure_id(u) for u in units],
                    "action": action}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def remove(self, target):
        def go():
            doc = self._doc()
            units = targets.resolve(doc, target, self._cache_for(doc))
            ids = [targets.ensure_id(u) for u in units]
            for u in units:
                targets.unit_objects(u).Clear()
            return {"removed": ids}
        return self._run(go)

    def list_atoms_bonds(self, target="selection"):
        """Enumerate atoms/bonds with stable refs in one call, so editing
        one doesn't require guessing an index first. atom_index/bond_index
        are still included (existing edit_atom/edit_bond calls can keep
        using them) but ref is the one worth keeping across calls — it
        survives other atoms/bonds being added or removed in between."""
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            out = []
            for u in targets.resolve(doc, target, cache):
                atoms, bonds = targets.unit_atoms_bonds(doc, u, cache)
                atom_entries = []
                for i, a in enumerate(atoms, start=1):
                    pos = a.Position
                    atom_entries.append({
                        "ref": targets.atom_ref(a),
                        "atom_index": i,
                        "element": t.element_symbol(a.ElementNumber),
                        "charge": a.Charge,
                        "x": round(pos.X, 2),
                        "y": round(pos.Y, 2),
                        "warning": a.ChemicalWarning or None,
                    })
                bond_entries = []
                for i, b in enumerate(bonds, start=1):
                    bond_entries.append({
                        "ref": targets.bond_ref(b),
                        "bond_index": i,
                        "order": t.bond_order_name(b.BondOrder),
                        "atom1_ref": targets.atom_ref(b.Atom1),
                        "atom2_ref": targets.atom_ref(b.Atom2),
                        "warning": b.ChemicalWarning or None,
                    })
                out.append({
                    "id": targets.ensure_id(u),
                    "atoms": atom_entries,
                    "bonds": bond_entries,
                })
            return {"structures": out}
        return self._run(go, timeout=SLOW_TIMEOUT)

    @staticmethod
    def _apply_atom_edit(doc, cache, edit):
        """One atom edit, shared by edit_atom and edit_atoms so both stay
        in lockstep. `edit`: {"target", "atom" (ref or 1-based index),
        "element"?, "charge"?, "set_charge"?}. Raises on an unresolvable
        target/atom — edit_atom lets that propagate as before, edit_atoms
        catches it per-item so one bad entry doesn't fail the whole batch."""
        unit = targets.resolve(doc, edit["target"], cache)[0]
        atom, idx = targets.resolve_atom(doc, unit, edit["atom"], cache)
        if edit.get("element"):
            atom.ElementNumber = t.element_number(edit["element"])
        if edit.get("set_charge"):
            atom.Charge = edit.get("charge", 0)
        return {
            "id": targets.ensure_id(unit),
            "atom_index": idx,
            "ref": targets.atom_ref(atom),
            "element": t.element_symbol(atom.ElementNumber),
            "charge": atom.Charge,
            "warning": atom.ChemicalWarning or None,
        }

    def edit_atom(self, target, atom_index, element=None, charge=None):
        edit = {"target": target, "atom": atom_index}
        if element is not None:
            edit["element"] = element
        if charge is not None:
            edit["set_charge"] = True
            edit["charge"] = charge

        def go():
            doc = self._doc()
            return self._apply_atom_edit(doc, self._cache_for(doc), edit)
        return self._run(go, timeout=SLOW_TIMEOUT)

    def edit_atoms(self, edits):
        """Apply many atom edits in ONE COM session instead of one MCP
        tool call each. edits: [{"target": object_id, "atom": ref or
        1-based index, "element"?: str, "charge"?: int, "set_charge"?:
        bool}, ...]. Mirrors move_objects: one shared backup, before/after
        diff so any change beyond the requested edits surfaces as
        `unexpected_changes` instead of being discovered later, and a
        failed item goes into `failed` rather than aborting the batch."""
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            backup = self._maybe_snapshot(doc)
            before = state.build_snapshot(doc)
            applied, failed = [], []
            for e in edits:
                try:
                    result = self._apply_atom_edit(doc, cache, e)
                    result["target"] = e.get("target")
                    result["atom"] = e.get("atom")
                    applied.append(result)
                except Exception as exc:
                    failed.append({"target": e.get("target"),
                                   "atom": e.get("atom"), "error": str(exc)})
            after = state.build_snapshot(doc)
            d = diff.diff_snapshots(before, after)
            requested_ids = {r["id"] for r in applied}
            unexpected = [m for m in d["modified"] if m["id"] not in requested_ids]
            return {
                "applied": applied,
                "failed": failed,
                "unexpected_changes": unexpected,
                "backup_path": backup,
            }
        return self._run(go, timeout=max(SLOW_TIMEOUT, 2.0 * len(edits)))

    @staticmethod
    def _apply_bond_edit(doc, cache, edit):
        """Bond-edit counterpart to _apply_atom_edit, shared by edit_bond
        and edit_bonds. `edit`: {"target", "bond" (ref or 1-based index),
        "bond_order"?}."""
        unit = targets.resolve(doc, edit["target"], cache)[0]
        bond, idx = targets.resolve_bond(doc, unit, edit["bond"], cache)
        if edit.get("bond_order"):
            bond.BondOrder = t.bond_order_value(edit["bond_order"])
        return {
            "id": targets.ensure_id(unit),
            "bond_index": idx,
            "ref": targets.bond_ref(bond),
            "bond_order": t.bond_order_name(bond.BondOrder),
            "warning": bond.ChemicalWarning or None,
        }

    def edit_bond(self, target, bond_index, bond_order=None):
        edit = {"target": target, "bond": bond_index}
        if bond_order is not None:
            edit["bond_order"] = bond_order

        def go():
            doc = self._doc()
            return self._apply_bond_edit(doc, self._cache_for(doc), edit)
        return self._run(go, timeout=SLOW_TIMEOUT)

    def edit_bonds(self, edits):
        """Batch counterpart to edit_bond; see edit_atoms for the shared
        rationale (one COM session, one backup, before/after diff, partial
        failure reported per-item). edits: [{"target": object_id, "bond":
        ref or 1-based index, "bond_order"?: str}, ...]."""
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            backup = self._maybe_snapshot(doc)
            before = state.build_snapshot(doc)
            applied, failed = [], []
            for e in edits:
                try:
                    result = self._apply_bond_edit(doc, cache, e)
                    result["target"] = e.get("target")
                    result["bond"] = e.get("bond")
                    applied.append(result)
                except Exception as exc:
                    failed.append({"target": e.get("target"),
                                   "bond": e.get("bond"), "error": str(exc)})
            after = state.build_snapshot(doc)
            d = diff.diff_snapshots(before, after)
            requested_ids = {r["id"] for r in applied}
            unexpected = [m for m in d["modified"] if m["id"] not in requested_ids]
            return {
                "applied": applied,
                "failed": failed,
                "unexpected_changes": unexpected,
                "backup_path": backup,
            }
        return self._run(go, timeout=max(SLOW_TIMEOUT, 2.0 * len(edits)))

    def add_atom(self, target, attach_to_atom_index, element, bond_order="single"):
        elem_num = t.element_number(element)
        order_val = t.bond_order_value(bond_order)

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            unit = targets.resolve(doc, target, cache)[0]
            anchor, _ = targets.resolve_atom(doc, unit, attach_to_atom_index, cache)
            new_atom = doc.MakeAtom()
            new_atom.ElementNumber = elem_num
            # Place near the anchor; Clean() afterwards fixes geometry.
            self._set_position(
                new_atom,
                anchor.Position.X + doc.Settings.BondLength,
                anchor.Position.Y,
            )
            bond = doc.MakeBond(anchor, new_atom)
            bond.BondOrder = order_val
            targets.unit_objects(unit).Clean(False)
            return {
                "id": targets.ensure_id(unit),
                "added_element": element,
                "bond_order": bond_order,
                "atom_count": len(targets.unit_atoms_bonds(doc, unit, cache)[0]),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    # ---------- stereochemistry ----------

    def get_stereochemistry(self, target="selection"):
        def go():
            doc = self._doc()
            out = []
            for u in targets.resolve(doc, target, self._cache_for(doc)):
                unit_atoms, unit_bonds = targets.unit_atoms_bonds(doc, u, self._cache_for(doc))
                atoms = []
                for i, a in enumerate(unit_atoms, start=1):
                    descriptor = t.ATOM_CIP_NAMES.get(int(a.Stereochemistry or 0))
                    if descriptor:
                        atoms.append({
                            "atom_index": i,
                            "element": t.element_symbol(a.ElementNumber),
                            "descriptor": descriptor,
                        })
                bonds = []
                for i, b in enumerate(unit_bonds, start=1):
                    descriptor = t.BOND_CIP_NAMES.get(int(b.Stereochemistry or 0))
                    display = t.bond_display_name(b.BondDisplay)
                    if descriptor or display != "plain":
                        bonds.append({
                            "bond_index": i,
                            "descriptor": descriptor,
                            "display": display,
                        })
                out.append({"id": targets.ensure_id(u), "atoms": atoms, "bonds": bonds})
            return {"stereochemistry": out}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def set_bond_stereo(self, target, bond_index, display):
        display_val = t.bond_display_value(display)

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            unit = targets.resolve(doc, target, cache)[0]
            bond, idx = targets.resolve_bond(doc, unit, bond_index, cache)
            bond.BondDisplay = display_val
            return {
                "id": targets.ensure_id(unit),
                "bond_index": idx,
                "ref": targets.bond_ref(bond),
                "display": t.bond_display_name(bond.BondDisplay),
                "note": "Verify the derived R/S with chemdraw_get_stereochemistry.",
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    # ---------- shorthand ----------

    @staticmethod
    def _reresolve_after_mutation(doc, old_id):
        """Contract/expand can rebuild a structure, orphaning its tag. If the
        old id still resolves, keep it; otherwise tag the newest untagged unit
        (the rebuilt structure) and report the id change."""
        try:
            targets.find_by_id(doc, old_id)
            return old_id, None
        except Exception:
            untagged = [u for u in targets.iter_units(doc)
                        if targets.get_id(u) is None]
            if not untagged:
                return None, f"structure {old_id} could not be re-identified"
            new_id = targets.ensure_id(untagged[-1])
            return new_id, f"structure was rebuilt; new id replaces {old_id}"

    def contract_to_shorthand(self, target="selection", label=""):
        # One COM submission per unit: looping ContractObjectsToLabel over
        # many units inside a single _run call can exceed the worker's
        # wedge-detection timeout on a large document (probed live — see
        # expand_shorthand) and falsely report ChemDraw as hung.
        #
        # The unit list is resolved ONCE up front and the live COM handles
        # are carried into each per-unit call directly — NOT re-looked-up by
        # id per call. targets.find_by_id rescans the whole document
        # (iter_units: one COM round trip per Group AND per Atom), so
        # re-deriving it inside every per-unit call would cost O(units x
        # document size) COM round trips instead of O(document size) once
        # (probed: ~800 round trips x 45 units = ~36k on a real scope file,
        # first-draft mistake caught by the user asking why the split was
        # slow). Every COM worker call runs on the same single dedicated
        # thread (com/worker.py), so holding a COM proxy across calls is
        # safe here; only the target of THIS call's own mutation goes stale
        # (handled by _reresolve_after_mutation), not the other units'.
        def prep():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            units = targets.resolve(doc, target, self._cache_for(doc))
            return doc, backup, [(u, targets.ensure_id(u)) for u in units]
        doc, backup, unit_pairs = self._run(prep, timeout=SLOW_TIMEOUT)

        def contract_one(unit, uid):
            objs = targets.unit_objects(unit)
            # ContractObjectsToLabel requires non-empty label text; default
            # to the structure's formula, like ChemDraw's own dialog does.
            text = label or objs.Formula or "R"
            objs.ContractObjectsToLabel(text)
            new_id, note = self._reresolve_after_mutation(doc, uid)
            entry = {"id": new_id, "label": text}
            if note:
                entry["note"] = note
            return entry

        out = [self._run(lambda u=u, uid=uid: contract_one(u, uid),
                         timeout=SLOW_TIMEOUT)
               for u, uid in unit_pairs]
        return {"contracted": out, "backup_path": backup}

    def expand_shorthand(self, target="selection"):
        # One COM submission per unit (see contract_to_shorthand for why,
        # and why the unit list — with live handles, not just ids — is
        # resolved once up front rather than re-looked-up per call).
        def prep():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            units = targets.resolve(doc, target, self._cache_for(doc))
            return doc, backup, [(u, targets.ensure_id(u)) for u in units]
        doc, backup, unit_pairs = self._run(prep, timeout=SLOW_TIMEOUT)

        def expand_one(unit, uid):
            targets.unit_objects(unit).ExpandLabelsToStructure()
            new_id, note = self._reresolve_after_mutation(doc, uid)
            entry = {"id": new_id}
            if note:
                entry["note"] = note
            return entry

        out = [self._run(lambda u=u, uid=uid: expand_one(u, uid),
                         timeout=SLOW_TIMEOUT)
               for u, uid in unit_pairs]
        return {"expanded": out, "backup_path": backup}

    def expand_labels(self, target="document", labels="all"):
        """Bulk-expand every contracted shorthand label across `target` in
        ONE COM call, optionally restricted to specific label text (e.g.
        just "Ph").

        Unlike contraction, this is provably safe to batch across many
        structures at once: ExpandLabelsToStructure() looks at whatever
        selection it's given and expands every already-distinct label ATOM
        found inside it, each back into its own structure — nothing gets
        merged, because each label is already its own separate object
        before the call. (Contraction is the opposite: ContractObjectsTo-
        Label welds its ENTIRE input into ONE new label, so batching it
        across structures corrupts them — probed live: two rings from two
        unrelated molecules selected together and contracted once produced
        a single malformed radical spanning both. Contraction must stay
        one call per match; see contract_functional_groups.)

        labels: "all" (default) expands every contracted label found;
        a comma-separated string or list restricts to label text matches,
        case-insensitive (e.g. "Ph" or "Ph,Bn") — this is how a request
        like "only expand the phenyl rings" is served.

        Finding label atoms is a single pass over doc.Atoms checking
        LabelText (empty for ordinary atoms, the display text — e.g. "Ph"
        — for a contracted nickname; probed live), NOT a per-unit rescan,
        so this scales as one document-size pass regardless of how many
        structures or labels are involved.
        """
        wanted = label_filter.parse_label_filter(labels)

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            units = targets.resolve(doc, target, self._cache_for(doc))
            allowed_ids = (None if target == "document" else
                          {targets.ensure_id(u) for u in units})

            doc.Objects.Unselect()
            counts = {}
            n_selected = 0
            for i in range(1, doc.Atoms.Count + 1):
                atom = doc.Atoms.Item(i)
                text = atom.LabelText
                if not text:
                    continue
                # An ordinary heteroatom (N, O, NH...) also has non-empty
                # LabelText — that's just how ChemDraw draws its symbol, not
                # a contracted shorthand label. NodeType is what actually
                # distinguishes them (probed live; see com/types.py).
                if atom.NodeType in t.NODE_TYPE_ORDINARY_ATOM:
                    continue
                if wanted is not None and text.strip().lower() not in wanted:
                    continue
                if allowed_ids is not None:
                    try:
                        frag = atom.Fragment
                    except Exception:
                        continue
                    if frag is None or targets.get_id(frag) not in allowed_ids:
                        continue
                atom.Selected = True
                n_selected += 1
                counts[text] = counts.get(text, 0) + 1

            if n_selected == 0:
                return {"expanded_labels": {}, "atoms_expanded": 0,
                        "backup_path": backup,
                        "note": "no matching contracted labels found in target"}

            doc.Selection.Objects.ExpandLabelsToStructure()
            # One re-tag pass for the whole batch (not per structure): a
            # combined multi-structure expansion doesn't have a clean
            # old-id -> new-id mapping the way a single-unit mutation does,
            # so every touched (and untouched) unit is simply re-scanned
            # once and any orphaned tag replaced.
            for u in targets.iter_units(doc, self._cache_for(doc)):
                targets.ensure_id(u)
            return {
                "expanded_labels": counts,
                "atoms_expanded": n_selected,
                "backup_path": backup,
            }
        return self._run(go, timeout=BULK_TIMEOUT)

    _MAX_CONTRACTIONS_PER_UNIT = 40  # hard stop against re-match loops
    _MAX_ROUNDS_PER_UNIT = 10  # export/match rounds; normally ~2 (1 productive + 1 empty confirm)

    def contract_functional_groups(self, target="selection", groups="auto"):
        """Find known functional groups in each target structure and contract
        just those atoms into their shorthand labels (Ph, Boc, TBS...).

        Detection: unit exported as CDXML (node ids == COM atom ids, probed
        live), matched with RDKit SMARTS in domain/substructures. Contraction:
        the matched atoms and their internal bonds are selected object-by-
        object via the settable IChemDrawObject.Selected, then the selection
        is collapsed with ContractObjectsToLabel — the same operation as
        selecting the ring by hand and using Structure > Contract Label.

        A "round" (one CDXML export + RDKit match) contracts EVERY
        non-overlapping match it finds in one structure, not just one — the
        matches are already guaranteed disjoint by find_contractions, and a
        single document-wide handle-map scan is built once and reused across
        all of them, instead of rescanning per match (probed live: this was
        the dominant cost — 574s for 38 contractions across 45 structures,
        ~15s/contraction, almost all of it redundant COM round-trips between
        matches, not the ContractObjectsToLabel calls themselves). A
        structure normally finishes in ~2 rounds regardless of how many
        groups it has (1 productive + 1 empty confirm), instead of one round
        per group.
        """
        shorthands = substructures.resolve_groups(groups)  # validate early

        # Each ROUND is its own worker submission (not each match, and never
        # multiple structures at once): one big closure covering a many-
        # structure document blows past the wedge-detection timeout and
        # reports ChemDraw as hung when it is merely busy.
        def prep():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            units = targets.resolve(doc, target, self._cache_for(doc))
            return backup, [(u, targets.ensure_id(u)) for u in units]
        backup, unit_pairs = self._run(prep, timeout=SLOW_TIMEOUT)

        results = []
        for unit, uid in unit_pairs:
            contracted, note = [], None
            current_unit, current_uid = unit, uid  # live handle valid for round 1 only
            for _ in range(self._MAX_ROUNDS_PER_UNIT):
                step = self._run(
                    lambda u=current_unit, uid=current_uid:
                        self._contract_round(u, uid, shorthands),
                    timeout=SLOW_TIMEOUT)
                if step.get("note"):
                    note = step["note"]
                contracted.extend(step.get("contracted", []))
                if not step.get("any_contracted") or step.get("new_id") is None:
                    current_uid = step.get("new_id", current_uid)
                    break
                current_uid, current_unit = step["new_id"], None  # force re-resolve next round
                if len(contracted) >= self._MAX_CONTRACTIONS_PER_UNIT:
                    break
            entry = {"id": current_uid, "contracted": contracted}
            if not contracted and note is None:
                entry["note"] = (
                    "no contractible groups found (rings drawn with an "
                    "aromatic circle instead of alternating bonds cannot "
                    "be matched)")
            elif note:
                entry["note"] = note
            results.append(entry)
        return {"results": results, "backup_path": backup}

    def _contract_round(self, unit, uid, shorthands):
        """Find and contract EVERY non-overlapping match in unit `uid` from
        ONE CDXML export/RDKit match pass. Worker thread only.

        `unit` is the live COM handle from prep() for round 1 only; pass
        None to force a fresh find_by_id lookup (used for round 2+, since
        ContractObjectsToLabel can rebuild a structure and orphan its own
        tag — same reasoning _reresolve_after_mutation already relies on).

        Returns {"any_contracted": bool, "contracted": [...], "new_id",
        "note"}.
        """
        doc = self._doc()
        if unit is None:
            unit = targets.find_by_id(doc, uid, self._cache_for(doc))
        cdxml = _com_text(
            targets.unit_objects(unit).GetData(t.mime_for("cdxml")))
        if not cdxml.strip():
            return {"any_contracted": False, "contracted": [],
                    "note": "structure exported no CDXML"}
        try:
            graph = cdxml_graph.parse(cdxml)
        except Exception as exc:
            return {"any_contracted": False, "contracted": [],
                    "note": f"CDXML export could not be parsed: {exc}"}
        if not any(n["is_real_atom"] for n in graph["nodes"]):
            return {"any_contracted": False, "contracted": [],
                    "note": "no drawn atoms"}
        try:
            mol, id_by_idx = substructures.build_mol(graph)
        except ValueError as exc:
            return {"any_contracted": False, "contracted": [], "note": str(exc)}
        found = substructures.find_contractions(mol, id_by_idx, shorthands)
        if not found:
            return {"any_contracted": False, "contracted": []}

        handle_map = self._build_handle_map(doc)
        contracted = []
        for match in found:
            try:
                self._contract_atom_ids_cached(
                    doc, handle_map, match["atom_ids"], match["label"],
                    match["bond_orders"])
            except _StaleHandleMap:
                # The one unverified assumption (cached handles from before
                # an earlier match's contraction still valid for a later,
                # disjoint match) turned out false this time -- rebuild once
                # and retry, rather than silently corrupting or hard-failing.
                handle_map = self._build_handle_map(doc)
                try:
                    self._contract_atom_ids_cached(
                        doc, handle_map, match["atom_ids"], match["label"],
                        match["bond_orders"])
                except _StaleHandleMap as exc:
                    raise ChemDrawError(
                        f"Could not select the {match['label']} group's "
                        f"atoms even after rebuilding the handle map — the "
                        f"structure may have changed unexpectedly mid-round: "
                        f"{exc}") from exc
            contracted.append({"label": match["label"],
                               "atoms_collapsed": len(match["atom_ids"])})

        new_id, note = self._reresolve_after_mutation(doc, uid)
        return {"any_contracted": True, "contracted": contracted,
                "new_id": new_id, "note": note}

    @staticmethod
    def _build_handle_map(doc):
        """One document-wide scan, reused across every match in a round
        instead of rescanning per match. Worker thread only."""
        atoms_by_id = {}
        for i in range(1, doc.Atoms.Count + 1):
            atom = doc.Atoms.Item(i)
            atoms_by_id[atom.ID] = atom
        bonds_by_pair = {}
        for i in range(1, doc.Bonds.Count + 1):
            bond = doc.Bonds.Item(i)
            bonds_by_pair[frozenset((bond.Atom1.ID, bond.Atom2.ID))] = bond
        graphics = []
        for i in range(1, doc.Graphics.Count + 1):
            g = doc.Graphics.Item(i)
            graphics.append((g.Left, g.Top, g.Right, g.Bottom, g))
        return {"atoms": atoms_by_id, "bonds": bonds_by_pair, "graphics": graphics}

    @staticmethod
    def _contract_atom_ids_cached(doc, handle_map, atom_ids, label, bond_orders):
        """Select exactly the given atoms (by doc-unique CDX id) plus the
        bonds between them, using handles from a pre-built _build_handle_map
        instead of rescanning doc.Atoms/doc.Bonds/doc.Graphics, then
        contract the selection to `label`.

        bond_orders [(id_a, id_b, kekulé order)] are written to the matched
        bonds first: rings displayed with an aromatic circle store order-1
        bonds, and contraction drops the circle — without explicit orders
        the fragment nested in the label degrades to its all-single-bond
        skeleton (probed live: PhEt became cyclohexyl-Et).

        Raises _StaleHandleMap if a cached id/handle turns out unusable (id
        missing from the map, or a dead COM proxy) — caller rebuilds the map
        and retries. Worker thread only."""
        wanted = set(atom_ids)
        doc.Objects.Unselect()
        try:
            matched_atoms = [handle_map["atoms"][aid] for aid in wanted]
        except KeyError as exc:
            raise _StaleHandleMap(f"atom id {exc} missing from cached map") from exc

        n_selected = 0
        xs, ys = [], []
        try:
            for atom in matched_atoms:
                atom.Selected = True
                n_selected += 1
                xs.append(atom.Position.X)
                ys.append(atom.Position.Y)
        except pywintypes.com_error as exc:
            raise _StaleHandleMap(str(exc)) from exc

        if n_selected != len(wanted):
            doc.Objects.Unselect()
            raise ChemDrawError(
                f"Only {n_selected} of {len(wanted)} atoms for the {label} "
                f"group could be selected — the structure changed underneath "
                f"the match. Nothing was contracted; re-run the tool.")

        try:
            for id_a, id_b, order in bond_orders:
                bond = handle_map["bonds"].get(frozenset((id_a, id_b)))
                if bond is None:
                    raise _StaleHandleMap(
                        f"bond {id_a}-{id_b} missing from cached map")
                if order in (1, 2, 3) and bond.BondOrder != order:
                    bond.BondOrder = order
                bond.Selected = True
        except pywintypes.com_error as exc:
            raise _StaleHandleMap(str(exc)) from exc

        # An aromatic ring drawn with a circle keeps that circle as a separate
        # doc.Graphics object; contraction would orphan it as a floating
        # circle. Take along any graphic fully inside the matched atoms' box.
        pad = 2.0
        left, top = min(xs) - pad, min(ys) - pad
        right, bottom = max(xs) + pad, max(ys) + pad
        try:
            for gleft, gtop, gright, gbottom, g in handle_map["graphics"]:
                if gleft >= left and gright <= right and gtop >= top and gbottom <= bottom:
                    g.Selected = True
        except pywintypes.com_error as exc:
            raise _StaleHandleMap(str(exc)) from exc

        doc.Selection.Objects.ContractObjectsToLabel(label)

    # ---------- publication layout ----------

    def _add_caption_to_unit(self, doc, unit, text, x, y, bold=False):
        cap = doc.MakeCaption()
        cap.Text = text
        if bold:
            try:
                cap.Face = 1
            except Exception:
                pass
        self._set_position(cap, x, y)
        try:
            cap.Group = unit  # caption joins the structure's group
        except Exception:
            pass
        return cap

    def arrange_grid(self, items, columns=None, layout="double-column",
                     page_width_in=None):
        """items: list of {object_id, label?}."""
        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            units = [targets.find_by_id(doc, it["object_id"], self._cache_for(doc)) for it in items]
            boxes = [
                layout_math.Box(u.Left, u.Top, u.Right, u.Bottom) for u in units
            ]
            page_w = layout_math.page_width_points(layout, page_width_in)
            positions, cols = layout_math.grid_positions(
                boxes, columns=columns, page_width=page_w)
            for unit, box, (tx, ty), it in zip(units, boxes, positions, items):
                objs = targets.unit_objects(unit)
                objs.Move(tx - box.left, ty - box.top)
                label = it.get("label")
                if label:
                    cx, cy = layout_math.caption_anchor(tx, ty, box)
                    self._add_caption_to_unit(doc, unit, label, cx, cy)
            return {
                "arranged": [it["object_id"] for it in items],
                "columns": cols,
                "page_width_points": page_w,
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def build_scope_table(self, entries, columns=None, layout="double-column",
                          page_width_in=None):
        """entries: list of {representation, format?, label?}. One call:
        validate -> insert all -> caption -> arrange."""
        validated = [
            (self._validate_input(e["representation"], e.get("format", "smiles")),
             e.get("format", "smiles"), e.get("label"))
            for e in entries
        ]

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            inserted = []
            for rep, fmt, label in validated:
                units = self._insert_structure_units(doc, rep, fmt)
                for u in units:
                    inserted.append((u, label))
            boxes = [
                layout_math.Box(u.Left, u.Top, u.Right, u.Bottom)
                for u, _ in inserted
            ]
            page_w = layout_math.page_width_points(layout, page_width_in)
            positions, cols = layout_math.grid_positions(
                boxes, columns=columns, page_width=page_w)
            ids = []
            for (unit, label), box, (tx, ty) in zip(inserted, boxes, positions):
                objs = targets.unit_objects(unit)
                objs.Move(tx - box.left, ty - box.top)
                ids.append(targets.ensure_id(unit))
                if label:
                    cx, cy = layout_math.caption_anchor(tx, ty, box)
                    self._add_caption_to_unit(doc, unit, label, cx, cy)
            return {
                "object_ids": ids,
                "columns": cols,
                "page_width_points": page_w,
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=max(SLOW_TIMEOUT, 5.0 * len(entries)))

    def autonumber(self, target="document", start=1, scheme="numeric",
                   bold=True):
        def go():
            doc = self._doc()
            units = targets.resolve(doc, target, self._cache_for(doc))
            # Reading order: top-to-bottom rows, then left-to-right.
            units.sort(key=lambda u: (round(u.Top / 40.0), u.Left))
            labels = numbering.make_labels(len(units), start=start, scheme=scheme)
            for unit, label in zip(units, labels):
                cx = (unit.Left + unit.Right) / 2.0
                self._add_caption_to_unit(
                    doc, unit, label, cx, unit.Bottom + 4.0, bold=bold)
            return {
                "numbered": [
                    {"id": targets.ensure_id(u), "label": lbl}
                    for u, lbl in zip(units, labels)
                ]
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def get_layout(self, target="document"):
        """Full layout snapshot for planning a reorganization: every
        structure's id/formula/bounds (scoped to `target`), every caption's
        text and which structure it belongs to (Caption.Group), and every
        panel rectangle on the page (doc.Graphics) — all in ONE COM round
        trip. Captions and boxes are always returned for the whole page
        (not scoped to `target`) since planning a move needs the
        surrounding context regardless of which structures are the actual
        subject.

        Built after a first reorganization attempt needed five-plus
        separate probes (bounds, then captions, then box rectangles, then
        group ownership discovered ad hoc) to assemble this same picture —
        the fix is gathering it all up front, once, so a plan can be
        computed entirely offline before any ChemDraw call is made.
        """
        def go():
            doc = self._doc()
            w, h = float(doc.Width or 540.0), float(doc.Height or 720.0)
            if target == "document":
                structures = state.build_snapshot(doc)
            else:
                structures = [state.describe_unit(u, w, h)
                             for u in targets.resolve(doc, target, self._cache_for(doc))]

            captions = []
            for i in range(1, doc.Captions.Count + 1):
                c = doc.Captions.Item(i)
                try:
                    grp = c.Group
                    group_id = targets.ensure_id(grp) if grp is not None else None
                except Exception:
                    group_id = None
                captions.append({
                    "text": c.Text or "",
                    "bounds": {"left": round(c.Left, 1), "top": round(c.Top, 1),
                              "right": round(c.Right, 1), "bottom": round(c.Bottom, 1)},
                    "group_id": group_id,
                })

            boxes = []
            for i in range(1, doc.Graphics.Count + 1):
                g = doc.Graphics.Item(i)
                boxes.append({
                    "index": i,
                    "bounds": {"left": round(g.Left, 1), "top": round(g.Top, 1),
                              "right": round(g.Right, 1), "bottom": round(g.Bottom, 1)},
                })

            return {"structures": structures, "captions": captions, "boxes": boxes}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def move_objects(self, moves):
        """Apply many independent moves in ONE COM session, instead of one
        MCP tool call per object — the batch-execution half of a
        reorganization plan computed offline against chemdraw_get_layout's
        output.

        moves: [{"object_id": ..., "dx": ..., "dy": ...}, ...]

        Probed live: moving one structure's Group can carry along an
        object that was NOT requested — an ion-pair counterion turned out
        to share some parent/child relationship with its cation that
        iter_units' own tagging didn't surface, so it moved for free when
        the cation moved, and then moved AGAIN (wrongly) when also
        targeted directly. Rather than assume this can't happen elsewhere,
        every call snapshots before and after and diffs them (the same
        domain.diff logic behind chemdraw_diff_since_last_check), so any
        object that moved WITHOUT being requested is reported back
        immediately as `unexpected_moves` — caught by the tool, not
        discovered later in a screenshot.
        """
        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            before = state.build_snapshot(doc)

            # Resolve every target ONCE via a single document-wide scan,
            # not once per move (same lesson as contract_functional_groups'
            # earlier find_by_id-per-pass inefficiency).
            by_id = {targets.ensure_id(u): u for u in targets.iter_units(doc, self._cache_for(doc))}
            applied, missing = [], []
            for mv in moves:
                oid = mv["object_id"]
                unit = by_id.get(oid)
                if unit is None:
                    missing.append(oid)
                    continue
                targets.unit_objects(unit).Move(mv.get("dx", 0.0), mv.get("dy", 0.0))
                applied.append(oid)

            after = state.build_snapshot(doc)
            d = diff.diff_snapshots(before, after)
            requested_ids = {mv["object_id"] for mv in moves}
            unexpected = [m for m in d["moved"] if m["id"] not in requested_ids]
            return {
                "applied": applied,
                "missing": missing,
                "unexpected_moves": unexpected,
                "diff": d,
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=max(SLOW_TIMEOUT, 2.0 * len(moves)))

    # ---------- style ----------

    def apply_style_preset(self, preset="ACS 1996"):
        values = get_preset(preset)

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            settings = doc.Settings
            applied, skipped = {}, {}
            for key, value in values.items():
                try:
                    setattr(settings, key, value)
                    applied[key] = value
                except Exception as exc:
                    skipped[key] = str(exc)
            try:
                settings.ApplySettings()
                restyled = True
            except Exception:
                restyled = False
            return {
                "preset": preset,
                "applied": applied,
                "skipped": skipped,
                "restyled_existing_objects": restyled,
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    @staticmethod
    def available_presets():
        return sorted(PRESETS)

    # ---------- reaction schemes ----------

    def make_reaction_scheme(self, reactants, products, reagents_text=None,
                             fmt="smiles"):
        reactants = [self._validate_input(r, fmt) for r in reactants]
        products = [self._validate_input(p, fmt) for p in products]

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            x, y = 60.0, 120.0
            gap = 24.0
            ids = []

            def place(rep):
                nonlocal x
                units = self._insert_structure_units(doc, rep, fmt)
                for u in units:
                    objs = targets.unit_objects(u)
                    objs.Move(x - u.Left, y - (u.Top + u.Bottom) / 2.0)
                    ids.append(targets.ensure_id(u))
                    x = u.Right + gap

            def plus():
                nonlocal x
                cap = doc.MakeCaption()
                cap.Text = "+"
                self._set_position(cap, x, y)
                x += 18.0

            for i, r in enumerate(reactants):
                if i:
                    plus()
                place(r)

            arrow_len = 70.0
            arrow_ok = False
            try:
                arrow = doc.MakeArrow()
                self._set_position(arrow, x + arrow_len / 2.0, y)
                arrow_ok = True
            except Exception:
                cap = doc.MakeCaption()
                cap.Text = "→"
                self._set_position(cap, x + arrow_len / 2.0, y)
            if reagents_text:
                cap = doc.MakeCaption()
                cap.Text = reagents_text
                self._set_position(cap, x + arrow_len / 2.0, y - 24.0)
            x += arrow_len + gap

            for i, p in enumerate(products):
                if i:
                    plus()
                place(p)

            return {
                "object_ids": ids,
                "arrow_native": arrow_ok,
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    # ---------- derivative enumeration (RDKit, no canvas) ----------

    def enumerate_derivatives(self, substituents, scaffold=None, fmt="smiles",
                              properties=("mw", "formula")):
        if scaffold is None:
            exported = self.export_structure("smiles", "selection")["structures"]
            if not exported:
                raise InvalidInputError(
                    "No scaffold given and nothing selected in ChemDraw.")
            scaffold = exported[0]["data"]
        elif fmt == "molfile":
            from rdkit import Chem
            mol = Chem.MolFromMolBlock(scaffold)
            if mol is None:
                raise InvalidInputError("Scaffold molfile failed RDKit parsing")
            scaffold = Chem.MolToSmiles(mol)
        rows, failures = enumeration.enumerate_derivatives(
            scaffold, substituents, list(properties))
        return {
            "scaffold": scaffold,
            "derivatives": rows,
            "failed": failures,
            "count": len(rows),
        }

    @staticmethod
    def export_data_table(rows, path, fmt="csv"):
        if fmt != "csv":
            raise ValueError("Only csv is supported")
        if not rows:
            raise ValueError("No rows to write")
        fieldnames = list(dict.fromkeys(k for row in rows for k in row))
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return {"path": os.path.abspath(path), "rows": len(rows)}
