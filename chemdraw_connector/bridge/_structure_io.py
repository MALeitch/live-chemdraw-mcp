"""Structure insertion/export: SMILES/name/molfile/etc in, image/text/
clipboard out."""
import base64

from .. import targets
from ..com import types as t
from ..errors import ChemDrawError
from ._plumbing import SLOW_TIMEOUT, _com_bytes, _com_text


class _StructureIO:
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
