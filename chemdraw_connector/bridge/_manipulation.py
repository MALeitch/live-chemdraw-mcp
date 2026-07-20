"""Structure/sub-selection manipulation: bond-split planning, transform
(move/rotate/scale/flip/clean), atom/bond editing, atom addition."""
from .. import state, targets
from ..com import types as t
from ..domain import bond_split, diff
from ..errors import InvalidInputError
from ._plumbing import SLOW_TIMEOUT

# edit_atoms/edit_bonds scale their timeout with batch size (max(SLOW_TIMEOUT,
# 2.0 * len(edits))) so a big-but-legitimate batch gets enough runway. With no
# upper bound on len(edits) that same scaling turns an oversized batch into a
# multi-hour timeout instead of a clear rejection. Same cap value as
# _enumeration.py's _MAX_SUBSTITUENTS for a similar unbounded-batch input.
_MAX_BATCH_EDITS = 500


class _Manipulation:
    def split_at_bond(self, target, bond_ref, side_atom_ref):
        """Read-only: which atoms/bonds lie on one side of a bond, without
        touching the document. Feed atom_refs/bond_refs straight into
        transform to move/flip just that side — the offline-planning step
        for folding a branch instead of rescaling a whole structure."""
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            units = targets.resolve(doc, target, cache)
            if len(units) != 1:
                raise InvalidInputError(
                    f"split_at_bond needs exactly one structure, got "
                    f"{len(units)} for target {target!r}."
                )
            unit = units[0]
            atoms, bonds = targets.unit_atoms_bonds(doc, unit, cache)
            bond, _ = targets.resolve_bond(doc, unit, bond_ref, cache)
            side_atom, _ = targets.resolve_atom(doc, unit, side_atom_ref, cache)
            try:
                result = bond_split.split_atoms(
                    [a.ID for a in atoms],
                    [(b, b.Atom1.ID, b.Atom2.ID) for b in bonds],
                    bond.Atom1.ID, bond.Atom2.ID, side_atom.ID)
            except bond_split.RingBondSplitError as exc:
                raise InvalidInputError(str(exc)) from exc
            atoms_by_id = {a.ID: a for a in atoms}
            return {
                "id": targets.ensure_id(unit),
                "atom_refs": [targets.atom_ref(atoms_by_id[aid])
                             for aid in result["atom_ids"]],
                "bond_refs": [targets.bond_ref(b) for b in result["bond_ids"]],
                "atom_count": len(result["atom_ids"]),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    @staticmethod
    def _capture_selection(doc):
        """Record every currently-selected atom/bond/graphic (by live COM
        reference), so a temporary selection built for one operation (see
        transform's atom_refs/bond_refs path) can restore the user's own
        selection afterward instead of silently clobbering whatever they
        had highlighted. Doc-scoped .Item() access only, same pattern
        already proven safe in _build_handle_map — unlike unit-scoped
        collections', which crash (see targets.unit_atoms_bonds)."""
        selected = []
        for i in range(1, doc.Atoms.Count + 1):
            a = doc.Atoms.Item(i)
            if a.Selected:
                selected.append(a)
        for i in range(1, doc.Bonds.Count + 1):
            b = doc.Bonds.Item(i)
            if b.Selected:
                selected.append(b)
        for i in range(1, doc.Graphics.Count + 1):
            g = doc.Graphics.Item(i)
            if g.Selected:
                selected.append(g)
        return selected

    @staticmethod
    def _restore_selection(doc, selected):
        doc.Objects.Unselect()
        for obj in selected:
            try:
                obj.Selected = True
            except Exception:
                pass  # may have been deleted/changed by the operation just run

    @staticmethod
    def _apply_transform_action(objs, action, dx, dy, degrees, factor, vertical):
        """Shared by transform's whole-unit and sub-selection paths so both
        run the identical dispatch against whatever IChemDrawObjects
        collection they're given (a unit's own .Objects, or an ad hoc
        doc.Selection.Objects built from arbitrary atom/bond refs)."""
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

    @staticmethod
    def _atom_cip_descriptors(doc, unit, cache):
        """R/S per atom for one unit, same read as get_stereochemistry's
        loop body — used to detect a flip silently inverting a stereocenter
        (confirmed live: ChemDraw's Flip mirrors the depiction but leaves
        wedge-begin/wedge-end unchanged, which inverts the CIP descriptor)."""
        unit_atoms, _ = targets.unit_atoms_bonds(doc, unit, cache)
        return [t.ATOM_CIP_NAMES.get(int(a.Stereochemistry or 0))
                for a in unit_atoms]

    def transform(self, target="selection", action="clean", dx=0.0, dy=0.0,
                  degrees=0.0, factor=1.0, vertical=False,
                  atom_refs=None, bond_refs=None):
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)

            if atom_refs or bond_refs:
                units = targets.resolve(doc, target, cache)
                if len(units) != 1:
                    raise InvalidInputError(
                        f"atom_refs/bond_refs need exactly one target "
                        f"structure, got {len(units)} for target {target!r}."
                    )
                unit = units[0]
                atoms, bonds = targets.unit_atoms_bonds(doc, unit, cache)
                wanted_atoms = set(atom_refs or [])
                wanted_bonds = set(bond_refs or [])
                backup = self._maybe_snapshot(doc)
                before = state.build_snapshot(doc, self._cache_for(doc))

                prior_selection = self._capture_selection(doc)
                doc.Objects.Unselect()
                n_selected = 0
                for a in atoms:
                    if targets.atom_ref(a) in wanted_atoms:
                        a.Selected = True
                        n_selected += 1
                for b in bonds:
                    if targets.bond_ref(b) in wanted_bonds:
                        b.Selected = True
                        n_selected += 1
                try:
                    if n_selected == 0:
                        raise InvalidInputError(
                            "None of the given atom_refs/bond_refs resolved "
                            f"within target {target!r}."
                        )
                    self._apply_transform_action(
                        doc.Selection.Objects, action, dx, dy, degrees, factor, vertical)
                finally:
                    # Guaranteed even on failure — a bad action name or an
                    # empty match must not leave the user's own selection
                    # clobbered by our temporary one.
                    self._restore_selection(doc, prior_selection)

                after = state.build_snapshot(doc, self._cache_for(doc))
                d = diff.diff_snapshots(before, after)
                requested_ids = {targets.ensure_id(unit)}
                unexpected = [m for m in d["modified"] + d["moved"]
                             if m["id"] not in requested_ids]
                return {
                    "transformed": [targets.ensure_id(unit)],
                    "action": action,
                    "atom_refs": sorted(wanted_atoms),
                    "bond_refs": sorted(wanted_bonds),
                    "unexpected_changes": unexpected,
                    "backup_path": backup,
                }

            units = targets.resolve(doc, target, cache)
            for u in units:
                self._apply_transform_action(
                    targets.unit_objects(u), action, dx, dy, degrees, factor, vertical)
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
        if len(edits) > _MAX_BATCH_EDITS:
            raise InvalidInputError(
                f"edit_atoms got {len(edits)} edits, over the "
                f"{_MAX_BATCH_EDITS}-edit limit for one call. Split the "
                "edits into smaller batches and retry."
            )

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            backup = self._maybe_snapshot(doc)
            before = state.build_snapshot(doc, self._cache_for(doc))
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
            after = state.build_snapshot(doc, self._cache_for(doc))
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
        if len(edits) > _MAX_BATCH_EDITS:
            raise InvalidInputError(
                f"edit_bonds got {len(edits)} edits, over the "
                f"{_MAX_BATCH_EDITS}-edit limit for one call. Split the "
                "edits into smaller batches and retry."
            )

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            backup = self._maybe_snapshot(doc)
            before = state.build_snapshot(doc, self._cache_for(doc))
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
            after = state.build_snapshot(doc, self._cache_for(doc))
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
