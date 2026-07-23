"""Native ChemDraw Stoichiometry Grid read/write, via the document's own
CDXML text -- NOT the live COM object model, which is confirmed broken for
this feature (see domain/stoichiometry_cdxml.py's module docstring for the
full investigation: doc.StoichiometryGrids.Item() is unconditionally
E_NOTIMPL, a re-fetched grid's .Components always raises
DISP_E_MEMBERNOTFOUND, and IChemDrawSGProperty.Text has no working setter
even via a raw protocol-level invoke)."""
import datetime
import os
import re
import tempfile
import uuid

from .. import snapshots, targets
from ..domain import layout_math
from ..domain import stoichiometry_cdxml as sc
from ..errors import ChemDrawError, InvalidInputError, TargetNotFoundError
from ._plumbing import SLOW_TIMEOUT

# CONFIRMED LIVE LANDMINE: Documents.Open() silently returns None (no
# exception, Documents.Count doesn't even increase) for a file sitting in
# a freshly-created folder directly under %LOCALAPPDATA% -- reproduced
# even for a previously-good, valid backup file already on disk under the
# connector's own existing snapshots.BACKUP_DIR
# (%LOCALAPPDATA%\chemdraw-mcp\backups), so this isn't specific to a new
# "stoichiometry_writes" folder name or to freshly-written content. A new
# folder under %TEMP% (an already-established, presumably OS/ChemDraw-
# trusted tree) opens the identical file fine. Use tempfile's own temp
# root rather than LOCALAPPDATA directly for anything this connector
# needs to hand back to Documents.Open().
WRITE_DIR = os.path.join(tempfile.gettempdir(), "chemdraw-mcp-stoichiometry")


def _format_value(value):
    """SGDataValue's raw attribute format: an integer-looking float is
    written without a trailing ".0" (confirmed live: Equivalents=1 read
    back as SGDataValue="1", not "1.0")."""
    f = float(value)
    return str(int(f)) if f == int(f) else repr(f)


_STOICH_SUFFIX_RE = re.compile(r"(-stoich-\d{8}-\d{6}-[0-9a-f]{6})+$")


def _write_base_name(doc_name):
    """The base filename a new stoichiometry-edit document is named from.

    Strips any prior "-stoich-<timestamp>-<hash>" suffix first -- without
    this, editing the same document repeatedly (each edit's
    new_active_document becomes the NEXT edit's doc.name, since that's
    what Document.Close()-is-a-no-op forces this tool to do) accumulates
    "-stoich-...-stoich-...-stoich-..." onto the filename forever instead
    of staying at one suffix."""
    base = re.sub(r"\.cdxml$", "", doc_name or "untitled", flags=re.IGNORECASE)
    base = _STOICH_SUFFIX_RE.sub("", base)
    return re.sub(r"[^\w.-]+", "_", base)


class _Stoichiometry:
    def make_stoichiometry_table(self, structure_ids):
        """Build a native Stoichiometry Grid from EXISTING structures
        already on the canvas (structure_ids from chemdraw_insert_structure
        or chemdraw_get_document_state -- at least 2 required).

        CONFIRMED LIVE: MakeStoichiometryGrid() needs an Arrow present in
        the selection alongside the structure Groups -- calling it with
        only plain Groups selected (no arrow) throws a hard COM exception
        ("The server threw an exception"), not a silent no-op or a usable
        empty grid. So this always draws one simple arrow spanning the
        selected structures' combined bounding box first -- a real,
        chemically normal part of a reaction/stoichiometry figure, not a
        cosmetic throwaway. It's left in place rather than deleted:
        Arrow.Delete() has never been exercised or verified safe anywhere
        in this connector, and this project's own precedent (see
        bridge/_specialty_objects.py's bracket/TLC-plate notes) is to
        never trust an unverified COM mutation, so an untested Delete()
        call isn't worth the risk for a purely cosmetic cleanup.

        Returns the new grid's grid_index (for chemdraw_edit_stoichiometry_
        table) plus the arrow's object_id."""
        def go():
            if len(structure_ids) < 2:
                raise InvalidInputError(
                    "Need at least 2 structure_ids to build a "
                    "stoichiometry table (got "
                    f"{len(structure_ids)})."
                )
            doc = self._doc()
            cache = self._cache_for(doc)
            units = [targets.find_by_id(doc, sid, cache)
                    for sid in structure_ids]
            backup = self._maybe_snapshot(doc)

            for u in units:
                u.Selected = True
            left = min(u.Left for u in units)
            right = max(u.Right for u in units)
            # Below the reactants' own combined footprint, not through
            # their shared vertical center -- confirmed live the old
            # cy = (min(Top)+max(Bottom))/2 placement drew the arrow
            # straight through both structures' own drawings (their
            # center IS inside their bounding boxes), not in a clean gap
            # below them. 20pt margin matches this codebase's other
            # inter-element gaps (e.g. layout_math.caption_anchor's
            # default 12pt, arrange_grid's v_gap default 8pt -- a bit
            # more breathing room here since a whole structure sits right
            # above, not just a caption).
            y = max(u.Bottom for u in units) + 20.0
            arrow = doc.MakeArrow()
            # Start carries the arrowhead by default (confirmed live
            # elsewhere in this codebase -- see bridge._reaction's own
            # arrow construction) -- putting the RIGHT point at Start
            # points the arrow rightward, the same reading-direction
            # convention chemdraw_make_reaction_scheme's own arrow uses,
            # not backwards away from the reactants (confirmed live: the
            # old left-point-at-Start setup pointed the arrowhead away
            # from the reaction, back past the leftmost reactant).
            sp = arrow.Start
            sp.X, sp.Y = right, y
            arrow.Start = sp
            ep = arrow.End
            ep.X, ep.Y = left, y
            arrow.End = ep
            arrow.Selected = True

            try:
                grid = doc.MakeStoichiometryGrid()
            except Exception as exc:
                raise ChemDrawError(
                    "ChemDraw rejected creating the stoichiometry grid "
                    f"from this selection: {exc}"
                ) from exc
            grid_id = str(grid.ID)
            component_count = grid.Components.Count

            text = snapshots.export_cdxml_text(doc)
            grid_index = None
            if text is not None:
                for g in sc.parse_grids(text):
                    if g["grid_id"] == grid_id:
                        grid_index = g["grid_index"]
                        break

            # Every other layout-mutating tool here (arrange_grid,
            # build_scope_table, make_reaction_scheme) reports
            # violations.off_page rather than trusting the layout blindly
            # -- this one didn't, so a badly-placed arrow/grid could land
            # off the real page with no signal at all. The grid itself
            # has no queryable bounds via COM (see this module's own
            # docstring on StoichiometryGrids being broken for re-access),
            # so only the arrow and the reactant/product structures
            # themselves are checked.
            arrow_id = targets.ensure_id(arrow)
            off_page_boxes = [layout_math.Box(left, y - 1.0, right, y + 1.0)]
            off_page_ids = [arrow_id]
            for u in units:
                off_page_boxes.append(layout_math.Box(u.Left, u.Top, u.Right, u.Bottom))
                off_page_ids.append(targets.ensure_id(u))
            off_page = layout_math.page_overflow(
                off_page_boxes, off_page_ids,
                float(doc.Width or 540.0), float(doc.Height or 720.0))

            return {
                "grid_index": grid_index,
                "grid_id": grid_id,
                "component_count": component_count,
                "arrow_object_id": arrow_id,
                "violations": {"off_page": off_page},
                "backup_path": backup,
                "note": (
                    "Call chemdraw_read_stoichiometry_table to see the "
                    "new grid's fields, then chemdraw_edit_stoichiometry_"
                    "table with this grid_index to fill in reactant "
                    "quantities."
                ),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def _raw_id_map(self, doc, cache):
        """raw ChemDraw Group ID (str, matches CDXML's ComponentReferenceID)
        -> this connector's claude_id, for every addressable structure
        unit -- lets read/edit use the same claude_id strings every other
        tool in this connector uses, instead of raw numeric ChemDraw ids."""
        out = {}
        for u in targets.iter_units(doc, cache):
            try:
                out[str(u.ID)] = targets.ensure_id(u)
            except Exception:
                continue
        return out

    def read_stoichiometry_tables(self):
        """Read every native Stoichiometry Grid on the active document, via
        CDXML text (the only working access path). Each component is
        annotated with this connector's claude_id (structure_id) where its
        raw ComponentReferenceID matches an addressable structure -- None
        for the row-label header component, which is always read-only."""
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            text = snapshots.export_cdxml_text(doc)
            if text is None:
                return {"grids": []}
            id_map = self._raw_id_map(doc, cache)
            grids = sc.parse_grids(text)
            out_grids = []
            for grid in grids:
                out_components = []
                for comp in grid["components"]:
                    ref = comp["structure_ref_id"]
                    out_components.append({
                        "component_index": comp["component_index"],
                        "structure_id": id_map.get(ref) if ref else None,
                        "is_header": comp["is_header"],
                        "is_reactant": comp["is_reactant"],
                        "properties": {
                            v["field"]: {
                                "property_type": k,
                                "value": v["value"],
                                "text": v["text"],
                                "editable": v["editable"],
                                "visible": v["visible"],
                            }
                            for k, v in comp["properties"].items()
                        },
                    })
                out_grids.append({
                    "grid_index": grid["grid_index"],
                    "grid_id": grid["grid_id"],
                    "components": out_components,
                })
            return {"grids": out_grids}
        return self._run(go)

    def edit_stoichiometry_table(self, grid_index, edits):
        """Edit one or more fields on ONE stoichiometry grid (grid_index
        from chemdraw_read_stoichiometry_table) in a single batch, save the
        edited CDXML to a new file, and reopen it -- confirmed live that
        ChemDraw's own engine recalculates every dependent field correctly
        from an edited SGDataValue on load (a 5.00g -> 10.00g edit against
        MW=46.07 recalculated Reactant Moles to 217.07mmol on reopen).

        edits: list of {"structure_id": <claude_id -- required; the
        header/row-label component is always read-only and has no other
        way to be targeted>, "field": <name from
        chemdraw_read_stoichiometry_table's properties, e.g.
        "sample_mass">, "value": <number>}.

        IMPORTANT CAVEAT, confirmed live and not fixable from this side:
        Document.Close() is a no-op on this ChemDraw version, and
        reopening the SAME path just reactivates the stale in-memory
        window without re-reading disk (also confirmed live) -- so this
        ALWAYS opens a genuinely new document window from a fresh file
        path. The old window is left open with now-stale content; close it
        by hand in ChemDraw if you don't need it. Batch every value you
        want to change into ONE call rather than one call per field, to
        keep this to one new window per logical edit, not one per field."""
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            text = snapshots.export_cdxml_text(doc)
            if text is None:
                raise InvalidInputError(
                    "Could not export the active document's CDXML -- "
                    "nothing to edit."
                )
            ref_by_structure_id = {}
            for u in targets.iter_units(doc, cache):
                try:
                    ref_by_structure_id[targets.ensure_id(u)] = str(u.ID)
                except Exception:
                    continue

            resolved_edits = []
            applied = []
            for e in edits:
                structure_id = e.get("structure_id")
                field = e["field"]
                value = e["value"]
                if not structure_id:
                    raise InvalidInputError(
                        "edits[].structure_id is required (the header/"
                        "row-label component is read-only and has no "
                        "other way to be targeted)."
                    )
                if structure_id not in ref_by_structure_id:
                    raise TargetNotFoundError(structure_id)
                try:
                    property_type = sc.field_to_property_type(field)
                except ValueError as exc:
                    raise InvalidInputError(str(exc)) from exc
                resolved_edits.append({
                    "grid_index": grid_index,
                    "structure_ref_id": ref_by_structure_id[structure_id],
                    "property_type": property_type,
                    "new_value": _format_value(value),
                    "new_display_text": f"{float(value):.2f}",
                })
                applied.append({"structure_id": structure_id, "field": field,
                                "value": value})

            try:
                new_text = sc.apply_edits(text, resolved_edits)
            except ValueError as exc:
                raise InvalidInputError(str(exc)) from exc

            os.makedirs(WRITE_DIR, exist_ok=True)
            base = _write_base_name(doc.name)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            unique = uuid.uuid4().hex[:6]
            new_path = os.path.join(
                WRITE_DIR, f"{base}-stoich-{stamp}-{unique}.cdxml")
            with open(new_path, "w", encoding="utf-8") as f:
                f.write(new_text)

            new_doc = self._conn.app().Documents.Open(new_path)
            new_doc.Activate()
            self._doc_name = new_doc.name

            return {
                "edits_applied": applied,
                "new_active_document": new_doc.name,
                "new_path": new_path,
                "note": (
                    "A NEW ChemDraw document window was opened from the "
                    "edited file -- Document.Close() and reopening the "
                    "same path in place are both confirmed no-ops on this "
                    "ChemDraw version, so there is no way to refresh the "
                    "original window instead. Call chemdraw_close_document "
                    f"on {doc.name!r} (discard_changes=true, since it's "
                    "now superseded by the new window) if you no longer "
                    "need it."
                ),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)
