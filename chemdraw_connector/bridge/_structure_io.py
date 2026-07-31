"""Structure insertion/export: SMILES/name/molfile/etc in, image/text/
clipboard out."""
import base64
import os

from .. import snapshots, targets
from ..com import nudge
from ..com import types as t
from ..domain import layout_math
from ..domain import sdf as sdf_domain
from ..errors import ChemDrawError, InvalidInputError
from ._plumbing import SLOW_TIMEOUT, _com_bytes, _com_text


class _StructureIO:
    def insert_structure(self, representation, fmt="smiles", position=None):
        representation = self._validate_input(representation, fmt)

        def go():
            doc = self._doc()
            units, wrappers = self._insert_structure_units(
                doc, representation, fmt, include_wrappers=True)
            if position and units:
                # A multi-fragment payload (a salt, a name-resolved
                # organometallic like PdCl2(PPh3)2) returns more than one
                # unit here -- one real ChemDraw Group per disconnected
                # fragment (see _split_wrapper_groups). Recentering EACH
                # fragment onto the same (x, y) individually (the old
                # behavior) stacked every ion/ligand exactly on top of each
                # other at one point -- confirmed live as the cause of
                # "all placed at identical/overlapping coordinates".
                # Instead, move the whole assembly by ONE shared delta so
                # ChemDraw's own relative placement between fragments (the
                # same spacing plan_reaction_layout's intra_group_gap
                # exists to preserve for reaction schemes) survives intact.
                x, y = position
                left = min(u.Left for u in units)
                top = min(u.Top for u in units)
                right = max(u.Right for u in units)
                bottom = max(u.Bottom for u in units)
                dx = x - (left + right) / 2.0
                dy = y - (top + bottom) / 2.0
                for u in units:
                    targets.unit_objects(u).Move(dx, dy)
            ids = [targets.ensure_id(u) for u in units]
            off_page = layout_math.page_overflow(
                [layout_math.Box(u.Left, u.Top, u.Right, u.Bottom) for u in units],
                ids, float(doc.Width or 540.0), float(doc.Height or 720.0))

            # A disconnected-fragment payload (an ionic salt like
            # "[Na+].[H-]") makes ChemDraw register an extra wrapper Group
            # around its own fragments -- real, permanent, but silently
            # excluded from `units` above (see _split_wrapper_groups) since
            # moving/positioning it alongside its own children double-
            # moves the same atoms. It's still a real object a caller may
            # want to reference as ONE reagent (e.g. one row in
            # chemdraw_make_stoichiometry_table, instead of two separate,
            # individually-wrong-MW ion rows) -- surfaced here, additively,
            # without changing `inserted`/`off_page`'s existing shape.
            wrapper_groups = []
            for w in wrappers:
                entry = self._describe_units(doc, [w["unit"]])[0]
                entry["wraps"] = [targets.ensure_id(c) for c in w["children"]]
                entry["note"] = (
                    "auto-created by ChemDraw around the disconnected "
                    "fragments listed in 'wraps' (e.g. an ionic salt's "
                    "ions) -- do NOT move/position this id independently, "
                    "it double-moves the same underlying atoms as its "
                    "children; safe to reference as one combined-reagent "
                    "row in chemdraw_make_stoichiometry_table etc."
                )
                wrapper_groups.append(entry)

            result = {"inserted": self._describe_units(doc, units),
                     "off_page": off_page,
                     "wrapper_groups": wrapper_groups}
            # Every OTHER document-mutating call in this codebase settles
            # ChemDraw's window/focus state afterward (new_document,
            # use_scratch_document, open_document, set_active_document —
            # see com/nudge.py's own docstring on why doc.Activate() alone
            # doesn't update real window focus/state) — this call was the
            # one exception. Confirmed live: a query immediately after
            # (chemdraw_get_document_state/chemdraw_status) could observe
            # stale Groups/Atoms/Bonds counts for one call as a result.
            nudge.bring_to_foreground(self._conn.hwnd)
            return result
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

    def export_cdxml(self, path, target="document", overwrite=False):
        def go():
            doc = self._doc()
            if target == "document":
                # Whole-document GetData("text/xml") -- the same call
                # write_backup_file already uses for backups, a complete,
                # valid CDXML page (header/colortable/fonttable included),
                # unlike export_structure(format="cdxml")'s per-unit
                # fragments from targets.resolve().
                text = snapshots.export_cdxml_text(doc)
            elif target == "selection":
                data = doc.Selection.Objects.GetData("text/xml")
                text = _com_text(data) if data else None
            else:
                raise InvalidInputError(
                    f"target must be 'document' or 'selection', got "
                    f"{target!r}"
                )
            if not text:
                raise ChemDrawError(
                    "ChemDraw returned no CDXML data" +
                    (" -- nothing is currently selected." if target == "selection" else "."))
            self._guard_write_path(path, overwrite)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return {"path": path, "bytes": len(text.encode("utf-8")),
                    "target": target}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def import_molfile(self, path, target="scratch"):
        """Insert every record from a .mol/.sdf file. Not itself wrapped in
        self._run -- composes use_scratch_document/insert_structure, each
        already its own worker submission (same composition-above-go()
        pattern tools/procedure_scope.py uses at the tool layer, just done
        here at the bridge layer instead)."""
        if not os.path.exists(path):
            raise InvalidInputError(
                f"No file found at {path!r}. Check the path and retry -- "
                "it must point to an existing .mol/.sdf file."
            )
        if not os.path.isfile(path):
            raise InvalidInputError(
                f"{path!r} exists but is not a file (e.g. a directory). "
                "Pass the path to the molfile/SDF itself."
            )
        if target not in ("scratch", "current"):
            raise InvalidInputError(
                f"target must be 'scratch' or 'current', got {target!r}"
            )
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        ext = os.path.splitext(path)[1].lower()
        if ext == ".sdf":
            records = sdf_domain.split_sdf_records(text)
        else:
            records = [text] if text.strip() else []
        if not records:
            raise InvalidInputError(
                f"No molecule records found in {path!r} (empty file, or "
                "an .sdf with no $$$$-terminated records)."
            )

        if target == "scratch":
            self.use_scratch_document()

        inserted, failed = [], []
        for i, record in enumerate(records):
            try:
                result = self.insert_structure(record, "molfile")
                inserted.append({"record_index": i, **result})
            except Exception as exc:
                failed.append({"record_index": i, "error": str(exc)})
        return {
            "path": path,
            "record_count": len(records),
            "inserted_count": len(inserted),
            "inserted": inserted,
            "failed": failed,
        }

    def export_image(self, fmt="png", target="selection", path=None, dpi=300,
                     overwrite=False):
        def go():
            doc = self._doc()
            if path:
                # Validate the write target FIRST -- before touching the
                # document's selection or paying for GetData's render.
                # FIXED: this guard used to run AFTER the multi-id-target
                # branch below mutated the live document's selection
                # (doc.Objects.Unselect() + setting .Selected on each
                # unit), so a call the guard went on to refuse had
                # already replaced whatever the user had selected in
                # ChemDraw -- a mutation from a call that reports failure.
                self._guard_write_path(path, overwrite)
            mime = t.mime_for(fmt)
            if target == "document":
                objs = doc.Objects
            else:
                units = targets.resolve(doc, target, self._cache_for(doc))
                if len(units) == 1:
                    objs = targets.unit_objects(units[0])
                else:
                    # unit.Selected = True (set directly on the unit, not
                    # unit_objects(u).Select()) — confirmed live that
                    # .Select() on a unit's Objects collection REPLACES the
                    # document's selection rather than adding to it, so a
                    # loop of .Select() calls silently ended up exporting
                    # only the LAST unit in a multi-id target. Setting
                    # .Selected directly on each unit is the proven-
                    # additive pattern (same as _contract_atom_ids_cached's
                    # atom/bond selection). Unselect() first so a target
                    # narrower than "everything currently selected" can't
                    # pick up something unrelated already selected in the
                    # live document.
                    doc.Objects.Unselect()
                    for u in units:
                        u.Selected = True
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
            # unit.Selected = True, not unit_objects(u).Select() — see
            # export_image: .Select() on a unit's Objects collection
            # REPLACES the document's selection instead of adding to it
            # (confirmed live), so a loop of .Select() calls used to
            # silently copy only the LAST unit in a multi-id target.
            # Unselect() first so an unrelated object already selected in
            # the live document doesn't get copied along with it.
            doc.Objects.Unselect()
            for u in units:
                u.Selected = True
            doc.Selection.Objects.Copy()
            return {"copied": [targets.ensure_id(u) for u in units]}
        return self._run(go)
