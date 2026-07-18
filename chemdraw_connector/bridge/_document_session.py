"""Document/session lifecycle: connection status, opening/creating/switching
documents, the shared scratch document, save/undo/redo."""
import os

from .. import snapshots, state
from ..domain import canvas
from ..errors import ChemDrawError
from ._plumbing import SLOW_TIMEOUT


class _DocumentSession:
    def status(self):
        def go():
            info = self._conn.info()
            app = self._conn.app()
            doc = app.ActiveDocument
            info["active_document"] = doc.name if doc is not None else None
            info["open_documents"] = app.Documents.Count
            if doc is not None:
                snap = state.build_snapshot(doc, self._cache_for(doc))
                real, wrapper_map, others = canvas.classify_units(snap)
                boxes = self._graphics_boxes(doc)
                info["structures_on_page"] = len(real)
                info["excluded_units"] = {
                    "caption_wrapper_duplicates": len(wrapper_map),
                    "decoration_groups": len(others),
                }
                info["captions_on_page"] = doc.Captions.Count
                info["boxes_on_page"] = len(boxes)
                per_box = {}
                unboxed = 0
                for s in real:
                    idx = (canvas.containing_box(s["bounds"], boxes)
                           if s.get("bounds") else None)
                    if idx is None:
                        unboxed += 1
                    else:
                        per_box[idx] = per_box.get(idx, 0) + 1
                info["structures_per_box"] = [
                    {"box_index": b["index"],
                     "structure_count": per_box.get(b["index"], 0)}
                    for b in boxes
                ]
                if unboxed:
                    info["structures_per_box"].append(
                        {"box_index": None, "structure_count": unboxed})
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
