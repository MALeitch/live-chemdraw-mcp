"""Free-floating annotation objects: reaction/mechanism arrows and symbols
(lone pairs, radicals, TS daggers, racemic/absolute/relative stereo-
descriptor labels). Neither lives in doc.Groups/doc.Atoms/doc.Fragment the
way a chemical structure does -- each has its own document-level collection
(doc.Arrows, doc.Symbols), addressed via targets.iter_annotations/
find_annotation_by_id rather than targets.resolve/iter_units. Tagging still
goes through the same ensure_id/MakeObjectTag mechanism proven live on
non-Group objects (see targets.tag_caption_owner).

Known limitation, not solved here: these are placed at a fixed point and do
NOT move if a nearby structure is later moved by chemdraw_move_objects/
chemdraw_transform -- the same class of gap already documented for
captions. Curved/arc arrows and true double-object equilibrium pairs are
not implemented -- both were probed live and found to need more
investigation before a design commitment (see docs/com_typelib/README.md
and the plan this session produced)."""
from .. import targets
from ..com import types as t
from ..errors import InvalidInputError
from ._plumbing import SLOW_TIMEOUT


class _Annotations:
    def _resolve_point(self, doc, unit, cache, spec):
        """spec: a raw {"x":, "y":} point (document points, top-left
        origin), or an atom_ref/1-based index/bond_ref resolved against
        `unit`. A "bNN-NN"-shaped string resolves to that bond's midpoint;
        anything else (a bare index or an "aNN" ref) resolves as an atom --
        a bare index is inherently ambiguous between atom and bond, so it's
        always treated as an atom index, matching chemdraw_list_atoms'
        atom_index/bond_index being separate fields the caller already
        knows which one they're passing."""
        if isinstance(spec, dict):
            return float(spec["x"]), float(spec["y"])
        if unit is None:
            raise InvalidInputError(
                "start/end/anchor must be a {'x':, 'y':} point when no "
                "target structure is given to resolve an atom/bond ref "
                "against."
            )
        if isinstance(spec, str) and spec.startswith("b"):
            bond, _ = targets.resolve_bond(doc, unit, spec, cache)
            return ((bond.Atom1.Position.X + bond.Atom2.Position.X) / 2.0,
                     (bond.Atom1.Position.Y + bond.Atom2.Position.Y) / 2.0)
        atom, _ = targets.resolve_atom(doc, unit, spec, cache)
        return atom.Position.X, atom.Position.Y

    def make_arrow(self, target, start, end, head_type="solid",
                   start_head="full", tail_head="none", dashed=False,
                   bold=False, no_go=None, dipole=False):
        """Create a straight reaction/mechanism arrow between two points.

        start/end: each a {"x":, "y":} point in document points, or an
        atom_ref/1-based atom index/bond_ref resolved against `target` (a
        bond_ref resolves to its midpoint). Pass target="" if both
        start/end are raw points.

        head_type: solid | hollow | angle -- the FILL style of whichever
        end(s) have a head (see start_head/tail_head). Confirmed live: a
        freshly created arrow defaults to a normal single-headed "solid"
        arrow pointing from end toward start (start_head="full",
        tail_head="none") -- matching chemdraw_make_reaction_scheme's own
        plain arrows.

        start_head/tail_head: none | full | half_left | half_right.
        half_left/half_right render a genuine single-barb "fishhook"
        arrowhead (confirmed live via a large head size + 600 DPI
        screenshot) -- the classic single-electron-pushing mechanism arrow
        shape. Setting both ends to "full" produces a double-headed arrow.

        no_go: None | "cross" | "hash" -- marks the arrow as a crossed-out
        "this pathway does not occur" indicator.
        dipole: adds a dipole-arrow tail marker.

        Known limitation: this is a free-floating object anchored to a
        point, not bound to the atom/bond it was drawn near -- moving that
        structure later will NOT carry the arrow along (same class of gap
        documented for captions; chemdraw_move_objects has no knowledge of
        arrows). Curved/arc arrows and true double-object ⇌ equilibrium
        pairs are not yet supported -- both were probed live this session
        and found to need more investigation before a design commitment."""
        head_type_val = t.arrow_head_type_value(head_type)
        start_head_val = t.arrow_head_position_value(start_head)
        tail_head_val = t.arrow_head_position_value(tail_head)
        no_go_val = t.no_go_type_value(no_go) if no_go else None

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            unit = targets.resolve(doc, target, cache)[0] if target else None
            sx, sy = self._resolve_point(doc, unit, cache, start)
            ex, ey = self._resolve_point(doc, unit, cache, end)
            backup = self._maybe_snapshot(doc)
            arrow = doc.MakeArrow()
            start_pt = arrow.Start
            start_pt.X, start_pt.Y = sx, sy
            arrow.Start = start_pt
            end_pt = arrow.End
            end_pt.X, end_pt.Y = ex, ey
            arrow.End = end_pt
            arrow.ArrowHeadType = head_type_val
            arrow.ArrowHeadPositionStart = start_head_val
            arrow.ArrowHeadPositionTail = tail_head_val
            arrow.IsDashed = bool(dashed)
            arrow.IsBold = bool(bold)
            if no_go_val is not None:
                arrow.NoGoType = no_go_val
            if dipole:
                arrow.IsDipole = True
            oid = targets.ensure_id(arrow)
            return {
                "object_id": oid,
                "start": {"x": round(arrow.Start.X, 2), "y": round(arrow.Start.Y, 2)},
                "end": {"x": round(arrow.End.X, 2), "y": round(arrow.End.Y, 2)},
                "head_type": t.arrow_head_type_name(arrow.ArrowHeadType),
                "start_head": t.arrow_head_position_name(arrow.ArrowHeadPositionStart),
                "tail_head": t.arrow_head_position_name(arrow.ArrowHeadPositionTail),
                "no_go": t.no_go_type_name(arrow.NoGoType),
                "dipole": bool(arrow.IsDipole),
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def set_arrow_style(self, object_id, head_type=None, start_head=None,
                        tail_head=None, dashed=None, bold=None, no_go=None,
                        dipole=None):
        """Edit an existing arrow's style -- including one
        chemdraw_make_reaction_scheme created, which tags its arrow for
        exactly this. Only params explicitly passed (not None) are
        changed; see chemdraw_make_arrow for the vocabulary."""
        head_type_val = (t.arrow_head_type_value(head_type)
                         if head_type is not None else None)
        start_head_val = (t.arrow_head_position_value(start_head)
                          if start_head is not None else None)
        tail_head_val = (t.arrow_head_position_value(tail_head)
                         if tail_head is not None else None)
        no_go_val = t.no_go_type_value(no_go) if no_go is not None else None

        def go():
            doc = self._doc()
            arrow = targets.find_annotation_by_id(doc, "arrow", object_id)
            backup = self._maybe_snapshot(doc)
            if head_type_val is not None:
                arrow.ArrowHeadType = head_type_val
            if start_head_val is not None:
                arrow.ArrowHeadPositionStart = start_head_val
            if tail_head_val is not None:
                arrow.ArrowHeadPositionTail = tail_head_val
            if dashed is not None:
                arrow.IsDashed = bool(dashed)
            if bold is not None:
                arrow.IsBold = bool(bold)
            if no_go_val is not None:
                arrow.NoGoType = no_go_val
            if dipole is not None:
                arrow.IsDipole = bool(dipole)
            return {
                "object_id": object_id,
                "head_type": t.arrow_head_type_name(arrow.ArrowHeadType),
                "start_head": t.arrow_head_position_name(arrow.ArrowHeadPositionStart),
                "tail_head": t.arrow_head_position_name(arrow.ArrowHeadPositionTail),
                "dashed": bool(arrow.IsDashed),
                "bold": bool(arrow.IsBold),
                "no_go": t.no_go_type_name(arrow.NoGoType),
                "dipole": bool(arrow.IsDipole),
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def list_arrows(self, target="document"):
        """Enumerate every arrow in the document (arrows aren't scoped to
        a structure, so `target` is currently always the whole document)."""
        def go():
            doc = self._doc()
            out = []
            for a in targets.iter_annotations(doc, "arrow"):
                out.append({
                    "object_id": targets.ensure_id(a),
                    "start": {"x": round(a.Start.X, 2), "y": round(a.Start.Y, 2)},
                    "end": {"x": round(a.End.X, 2), "y": round(a.End.Y, 2)},
                    "head_type": t.arrow_head_type_name(a.ArrowHeadType),
                    "start_head": t.arrow_head_position_name(a.ArrowHeadPositionStart),
                    "tail_head": t.arrow_head_position_name(a.ArrowHeadPositionTail),
                })
            return {"arrows": out}
        return self._run(go)

    def make_symbol(self, target, symbol_type, anchor, offset_x=0.0, offset_y=0.0):
        """Place a symbol object anchored near a point.

        symbol_type: racemic | absolute | relative render immediately
        usefully as a clear boxed stereo-descriptor label ("Rac"/"Abs"/
        "Rel", confirmed live). lone_pair | electron | radical_cation |
        radical_anion | circle_plus | circle_minus | dagger |
        double_dagger | plus | minus all render at a tiny (confirmed live,
        sub-1pt bounds) default size with no COM-exposed way to resize
        found so far -- usable but may be barely visible in a printed
        figure; treat as experimental until a sizing mechanism is found.

        anchor: a {"x":, "y":} point, or an atom_ref/1-based index/
        bond_ref resolved against `target` (a bond_ref resolves to its
        midpoint). offset_x/offset_y: applied on top of the resolved
        anchor point (document points), since symbols have no automatic
        bond-avoidance placement.

        Known limitation: like chemdraw_make_arrow, this is a
        free-floating object -- it will NOT move if the anchor structure
        is moved later."""
        type_val = t.symbol_type_value(symbol_type)

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            unit = targets.resolve(doc, target, cache)[0] if target else None
            ax, ay = self._resolve_point(doc, unit, cache, anchor)
            backup = self._maybe_snapshot(doc)
            sym = doc.MakeSymbol(type_val)
            pos = sym.Position
            pos.X, pos.Y = ax + offset_x, ay + offset_y
            sym.Position = pos
            oid = targets.ensure_id(sym)
            return {
                "object_id": oid,
                "symbol_type": t.symbol_type_name(sym.SymbolType),
                "position": {"x": round(sym.Position.X, 2), "y": round(sym.Position.Y, 2)},
                "is_radical": bool(sym.IsRadical),
                "is_charge": bool(sym.IsCharge),
                "is_positive_charge": bool(sym.IsPositiveCharge),
                "is_negative_charge": bool(sym.IsNegativeCharge),
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def list_symbols(self, target="document"):
        """Enumerate every symbol in the document (symbols aren't scoped
        to a structure, so `target` is currently always the whole
        document)."""
        def go():
            doc = self._doc()
            out = []
            for s in targets.iter_annotations(doc, "symbol"):
                out.append({
                    "object_id": targets.ensure_id(s),
                    "symbol_type": t.symbol_type_name(s.SymbolType),
                    "position": {"x": round(s.Position.X, 2), "y": round(s.Position.Y, 2)},
                })
            return {"symbols": out}
        return self._run(go)
