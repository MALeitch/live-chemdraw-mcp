"""Structure/sub-selection manipulation: bond-split planning, transform
(move/rotate/scale/flip/clean), atom/bond editing, atom addition."""
from .. import state, targets
from ..com import types as t
from ..domain import bond_split, diff
from ..errors import InvalidInputError, TargetNotFoundError
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
    def _apply_transform_action(objs, action, dx, dy, degrees, factor, vertical,
                               de_novo=False):
        """Shared by transform's whole-unit and sub-selection paths so both
        run the identical dispatch against whatever IChemDrawObjects
        collection they're given (a unit's own .Objects, or an ad hoc
        doc.Selection.Objects built from arbitrary atom/bond refs).

        de_novo is ChemDraw's own Clean(deNovo) argument: False tidies the
        coordinates already present, True re-derives the layout from the
        connection table. Keyword-with-default so the callers that have no
        business re-laying anything out (rotate/flip in _layout, the
        post-contraction tidy in _shorthand) keep passing the positional
        seven and stay on the non-destructive path."""
        if action == "move":
            objs.Move(dx, dy)
        elif action == "rotate":
            objs.Rotate(degrees, True)
        elif action == "scale":
            objs.Scale(factor, True, True)
        elif action == "flip":
            objs.Flip(vertical, False)
        elif action == "clean":
            objs.Clean(bool(de_novo))
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
                      atom_refs=None, bond_refs=None, de_novo=False):
            """target may also be (or include) an arrow, symbol, bracket,
            tlc_plate, or (owned or unowned) caption id, not just a structure's
            — resolved via targets.resolve_any/find_removable_by_id (see that
            function's docstring for the "no longer exists" misdirection this
            replaces: the very same id chemdraw_list_arrows/
            chemdraw_get_document_state had just confirmed exists used to come
            back from here as TargetNotFoundError, wrongly implying it had
            been deleted). Only action="move" is meaningful for an annotation
            — ChemDraw's rotate/scale/flip/clean operate on chemical
            structure, which arrows/captions/etc. don't have — so any other
            action against an annotation target is reported per-item in
            `failed` with a clear reason instead of silently no-oping or
            raising a generic error. atom_refs/bond_refs (sub-selection) stay
            structure-only: an annotation has no atoms/bonds to select, so
            that combination is rejected with an explicit InvalidInputError
            naming which kind was actually resolved, rather than falling
            through to resolve()'s "no longer exists" for a target that in
            fact does exist, just not as a structure.
        
            WARNING: action="clean" with target="document" (or any target
            resolving to many structures) is UNSAFE above a handful of
            structures. ChemDraw's Clean(False) on a multi-structure collection
            treats them as one system to lay out together, causing superlinear
            cost — a 24-structure page took 785 s CPU and never returned, while
            per-unit clean finished all 24 in 0.8 s total. This fix makes
            transform() iterate per unit for action="clean" when the target
            resolves to multiple structures, reusing the same logic as
            _shorthand._clean_unit(). The old whole-document path is deprecated
            and will be removed.

            de_novo (action="clean" only) is ChemDraw's own Clean(deNovo)
            argument, which this connector previously hardcoded to False:
            False tidies the coordinates already there, True re-derives the
            layout from the connection table. Measured over 144 real
            hand-drawn structures, scoring each by the fraction of bonds more
            than 30% off its own median length: 49/144 badly drawn with no
            clean, 13/144 under deNovo=False, 0/144 under deNovo=True — and
            the stereo-bond count goes UP (93 -> 89 -> 97), not down. The
            weaker clean genuinely cannot rescue a squashed drawing (one
            structure went 0.83 -> 0.22 under False, -> 0.00 under True).
            The default stays False because deNovo=True discards the
            chemist's own arrangement, which is wrong for a hand-laid-out
            reaction scheme or a transition-state drawing and right for
            regularising bulk structures. de_novo=True with any other action
            is rejected rather than silently ignored — Move/Rotate/Scale/Flip
            have no such argument, so accepting it would imply a relayout
            that never happens.
            """
            if de_novo and action != "clean":
                raise InvalidInputError(
                    f"de_novo only applies to action='clean'; got "
                    f"action={action!r}. ChemDraw's Move/Rotate/Scale/Flip "
                    "have no de novo layout mode."
                )

            def go():
                doc = self._doc()
                cache = self._cache_for(doc)

                if atom_refs or bond_refs:
                    try:
                        units = targets.resolve(doc, target, cache)
                    except TargetNotFoundError:
                        # target may be a genuine annotation id (arrow,
                        # caption, ...) rather than something that no longer
                        # exists at all -- resolve() only ever searches
                        # structures, so it can't tell the two apart itself.
                        # Give the accurate reason when that's the case,
                        # instead of letting the misleading "no longer exists"
                        # message stand for an id that plainly does exist.
                        if isinstance(target, str):
                            try:
                                kind, _ = targets.find_annotation_by_id_any(doc, target)
                            except TargetNotFoundError:
                                raise
                            raise InvalidInputError(
                                f"atom_refs/bond_refs need a structure target, "
                                f"but {target!r} resolved to a {kind}, which "
                                "has no atoms or bonds to select. Use target="
                                f"{target!r} with action='move' and no "
                                "atom_refs/bond_refs to move it as a whole."
                            )
                        raise
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
                            doc.Selection.Objects, action, dx, dy, degrees,
                            factor, vertical, de_novo)
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
                    out = {
                        "transformed": [targets.ensure_id(unit)],
                        "action": action,
                        "atom_refs": sorted(wanted_atoms),
                        "bond_refs": sorted(wanted_bonds),
                        "unexpected_changes": unexpected,
                        "backup_path": backup,
                    }
                    if action == "clean":
                        # Echoed only where it means something: the caller
                        # cannot otherwise tell which of the two very
                        # different Clean modes actually ran.
                        out["de_novo"] = bool(de_novo)
                    return out

                resolved = targets.resolve_any(doc, target, cache)
                # For action="clean", if target resolves to multiple structures,
                # iterate per unit to avoid ChemDraw's superlinear whole-selection
                # Clean cost (measured 785s vs 0.8s for 24 structures).
                # Use the same per-unit approach as _shorthand._clean_unit().
                if action == "clean":
                    structure_units = [(kind, obj) for kind, obj in resolved if kind == "structure"]
                    if len(structure_units) > 1:
                        transformed, failed = [], []
                        for kind, obj in structure_units:
                            uid = targets.ensure_id(obj)
                            try:
                                # Reuse _clean_unit logic: apply clean to this unit's objects only
                                self._apply_transform_action(
                                    targets.unit_objects(obj), "clean", 0.0, 0.0, 0.0, 1.0, False,
                                    de_novo)
                                transformed.append(uid)
                            except Exception as exc:
                                failed.append({"id": uid, "error": str(exc)})
                        # Handle annotations (arrows, captions, etc.) - clean not supported
                        for kind, obj in resolved:
                            if kind != "structure":
                                uid = targets.ensure_id(obj)
                                failed.append({
                                    "id": uid,
                                    "error": (
                                        f"action='clean' is not supported for a "
                                        f"{kind} -- ChemDraw's clean applies to chemical "
                                        f"structure, which a {kind} doesn't have. Only "
                                        f"action='move' is supported for a {kind} target."
                                    ),
                                })
                        return {"transformed": transformed, "failed": failed,
                                "action": action, "de_novo": bool(de_novo)}
                
                transformed, failed = [], []
                for kind, obj in resolved:
                    uid = targets.ensure_id(obj)
                    if kind != "structure":
                        # Arrow/caption/symbol/bracket/tlc_plate -- only a
                        # whole-object move is supported (no Rotate/Scale/
                        # Flip/Clean equivalent through this connector; see
                        # this method's own docstring). Reported per-item,
                        # same isolation as a structure's Clean()/Rotate()/
                        # etc. failing below, so one unsupported action in a mixed
                        # batch doesn't discard whatever else transformed.
                        if action != "move":
                            failed.append({
                                "id": uid,
                                "error": (
                                    f"action={action!r} is not supported for a "
                                    f"{kind} -- ChemDraw's rotate/scale/flip/"
                                    "clean apply to chemical structure, which "
                                    f"a {kind} doesn't have. Only action='move' "
                                    f"is supported for a {kind} target."
                                ),
                            })
                            continue
                        if self._move_annotation(kind, obj, dx, dy):
                            transformed.append(uid)
                        else:
                            failed.append({
                                "id": uid,
                                "error": (
                                    f"Could not move {kind} {uid!r} -- its "
                                    "positional properties could not be read "
                                    "or written."
                                ),
                            })
                        continue
                    try:
                        self._apply_transform_action(
                            targets.unit_objects(obj), action, dx, dy, degrees,
                            factor, vertical, de_novo)
                        transformed.append(uid)
                    except Exception as exc:
                        # One unit's Clean()/Rotate()/etc. failing must not
                        # abort a multi-id batch and discard whatever earlier
                        # units already transformed — same per-item isolation
                        # as edit_atoms/edit_bonds/remove.
                        failed.append({"id": uid, "error": str(exc)})
                out = {"transformed": transformed, "failed": failed,
                       "action": action}
                if action == "clean":
                    out["de_novo"] = bool(de_novo)
                return out
            op_description = f"transform action={action}"
            if action == "clean" and de_novo:
                op_description += " de_novo=True"
            return self._run(go, timeout=SLOW_TIMEOUT, op_name="transform", op_description=op_description)

    def remove(self, target):
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            if target in ("document", "selection"):
                units = targets.resolve(doc, target, cache)
                removed, failed = [], []
                for u in units:
                    uid = targets.ensure_id(u)
                    try:
                        targets.unit_objects(u).Clear()
                        removed.append(uid)
                    except Exception as exc:
                        # ChemDraw's own group relationships can cascade a
                        # removal onto another unit in this same batch (e.g.
                        # a salt's counterion auto-clearing with its cation,
                        # see move_objects' docstring for the analogous
                        # "moved for free" case) -- that must not abort the
                        # whole call and hide which units already got
                        # removed before it.
                        failed.append({"id": uid, "error": str(exc)})
                return {"removed": removed, "failed": failed}

            # An explicit id (or list of ids) may name a real structure OR
            # a free-floating annotation (caption, arrow, symbol, bracket,
            # tlc_plate) -- unlike "document"/"selection" (always
            # structures only, by design), a single/explicit target is
            # exactly the case that used to leave a bad caption or TLC
            # plate permanently stuck: targets.resolve/find_by_id only
            # ever searched structure units, so there was no id space an
            # orphaned annotation could even be addressed through. See
            # targets.find_removable_by_id.
            ids = target if isinstance(target, (list, tuple)) else [target]
            removed, failed = [], []
            for oid in ids:
                try:
                    kind, obj = targets.find_removable_by_id(doc, oid, cache)
                    if kind == "structure":
                        targets.unit_objects(obj).Clear()
                    else:
                        obj.Delete()
                    removed.append(oid)
                except Exception as exc:
                    failed.append({"id": oid, "error": str(exc)})
            return {"removed": removed, "failed": failed}
        return self._run(go)

    def list_atoms_bonds(self, target="selection", elements=None, include_bonds=True):
        """Enumerate atoms/bonds with stable refs in one call, so editing
        one doesn't require guessing an index first. atom_index/bond_index
        are still included (existing edit_atom/edit_bond calls can keep
        using them) but ref is the one worth keeping across calls — it
        survives other atoms/bonds being added or removed in between.

        elements: optional list of element symbols (e.g. ["N", "F"]) to
        filter the returned atoms to -- added after a real pain point hit
        live: target="document" on a modest ~250-atom, 7-structure page
        returned a 60K-character dump (every atom's x/y/charge/isotope/
        warning, every bond, for every structure) that blew the tool
        result token limit, just to find "which atoms are nitrogen" for a
        batch color edit. Filtering server-side avoids the round trip
        through a saved-to-disk file + ad hoc Python/jq parsing that
        became the workaround. Invalid symbols raise INVALID INPUT before
        any COM work, same as t.element_number elsewhere. bond_index
        stays 1-based over the FULL unfiltered bond list even when
        elements is given -- filtering only touches which atoms are
        reported, never atom_index/bond_index numbering, so refs/indices
        from an unfiltered call remain valid to pass back in.

        include_bonds=False skips building bond_entries (per-bond
        bond_ref/bond_order_name/Atom1/Atom2 reads) for the same "I only
        actually needed atoms" case. NOTE: targets.unit_atoms_bonds below
        fetches atoms and bonds together as one unit-membership scan
        regardless of this flag, so include_bonds=False does not skip
        that underlying scan -- only the per-bond property reads and
        dict-building on top of it, which is still the bulk of the
        response-size and COM-property-read cost when bonds aren't
        needed."""
        wanted_numbers = None
        if elements is not None:
            wanted_numbers = {t.element_number(e) for e in elements}

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            units = targets.resolve(doc, target, cache)
            # bulk_unit_atoms_bonds, not a unit_atoms_bonds call per unit:
            # the per-unit version re-scans doc.Atoms/doc.Bonds in full for
            # EVERY unit it's asked about when uncached, so a per-unit loop
            # over a big target="document" pays that full-document scan
            # once per structure -- confirmed live to blow well past this
            # call's own timeout on a 200-structure/1200-atom document
            # (issue #29). bulk_unit_atoms_bonds does the equivalent shared
            # scan at most once for the whole target instead.
            atoms_bonds_by_uid = targets.bulk_unit_atoms_bonds(doc, units, cache)
            out = []
            for u in units:
                atoms, bonds = atoms_bonds_by_uid[targets.ensure_id(u)]
                atom_entries = []
                for i, a in enumerate(atoms, start=1):
                    if wanted_numbers is not None and a.ElementNumber not in wanted_numbers:
                        continue
                    pos = a.Position
                    atom_entries.append({
                        "ref": targets.atom_ref(a),
                        "atom_index": i,
                        "element": t.element_symbol(a.ElementNumber),
                        "charge": a.Charge,
                        "isotope": a.Isotope or None,
                        "x": round(pos.X, 2),
                        "y": round(pos.Y, 2),
                        "warning": a.ChemicalWarning or None,
                    })
                if wanted_numbers is not None and not atom_entries:
                    # A structure with none of the requested elements would
                    # otherwise show up as a hollow {"atoms": [], "bonds":
                    # [...]} entry -- noise identical in shape to "this
                    # structure legitimately has zero atoms" (impossible)
                    # or a real query bug. Dropping it keeps the filtered
                    # response's size actually proportional to the filter,
                    # which is the entire point of adding one. Checked
                    # BEFORE the bond_entries dict-building loop below, not
                    # just before returning, so a dropped structure skips
                    # those per-bond property reads rather than building
                    # bond_entries only to throw them away (the underlying
                    # unit_atoms_bonds scan above already ran either way --
                    # see include_bonds' docstring note).
                    continue
                bond_entries = []
                if include_bonds:
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
        "element"?, "charge"?, "set_charge"?, "isotope"?, "set_isotope"?,
        "color"?}. Raises on an unresolvable target/atom — edit_atom lets
        that propagate as before, edit_atoms catches it per-item so one
        bad entry doesn't fail the whole batch.

        isotope is a plain mass-number int (e.g. 13 for 13C, 2 for D),
        gated by set_isotope the same way charge is gated by set_charge —
        confirmed live that Atom.Isotope survives export as real isotope
        notation (SMILES `[13CH2]`, molfile `M  ISO` block), not just a
        ChemDraw-display-only label.

        color is an ordinary '#RRGGBB' hex string (e.g. '#FF0000' for
        red) -- t.rgb_hex_to_colorref converts it to the COLORREF int
        (0x00BBGGRR byte order) IChemDrawObject.Color actually takes, so
        callers never touch the BGR ordering directly. Confirmed live on
        Atom.Color specifically: it recolors the atom's LABEL TEXT (the
        "OH"/"N"/etc. glyph ChemDraw draws), not the vertex itself -- an
        ordinary carbon with no visible label (implicit, undrawn) shows
        no visible change even though the property still reads back
        correctly, which is not a bug, just Color having nothing to paint.
        Unlike Charge/Isotope, explicit '#000000' does NOT hit the
        "silently rejected when already nonzero" ChemDraw quirk
        documented above -- confirmed live 0->nonzero->0 round-trips
        cleanly on both Atom and Bond -- so color needs no set_color
        gate; None simply means "leave unchanged", any hex string
        including black is applied as given.

        highlighted is a plain bool on IChemDrawObject.Highlighted --
        confirmed live to round-trip cleanly True<->False (no
        set_highlighted gate needed, same reasoning as color). NOT
        confirmed to be ChemDraw's real "Highlight Color" GUI tool --
        an earlier claim here said combining highlighted=true with color
        reproduced it; that was wrong. What was actually observed:
        Highlighted=True with Color left at 0 renders the atom/bond in a
        fixed red; Highlighted=True with an explicit color set instead
        RECOLORS the drawn line/label text itself to that color (thick,
        opaque, replacing the normal black) -- indistinguishable in kind
        from just setting color alone, not a translucent wash BEHIND the
        original black structure the way ChemDraw's real Highlight Color
        tool renders (confirmed by the user directly, live, after seeing
        the actual output). Still real, still round-trips, still visibly
        different from plain color (the fixed-red-when-unset behavior is
        genuine) -- just not proven to be the same feature as the GUI
        tool. Treat highlighted as a distinct, lesser-understood property
        until the real mechanism is found, not as a highlight-color
        substitute."""
        unit = targets.resolve(doc, edit["target"], cache)[0]
        atom, idx = targets.resolve_atom(doc, unit, edit["atom"], cache)
        if edit.get("element"):
            atom.ElementNumber = t.element_number(edit["element"])
        if edit.get("color") is not None:
            atom.Color = t.rgb_hex_to_colorref(edit["color"])
        if edit.get("highlighted") is not None:
            atom.Highlighted = bool(edit["highlighted"])
        if edit.get("set_charge"):
            atom.Charge = edit.get("charge", 0)
        if edit.get("set_isotope"):
            # KNOWN LIMITATION, confirmed live, not solved here: writing
            # Isotope = 0 is silently rejected (readback keeps the prior
            # nonzero value, no exception) when the atom's current isotope
            # is already nonzero -- reproduced deterministically 5/5
            # attempts, not a transient flake. This is not isotope-specific:
            # Atom.Charge has the exact same "explicit 0 rejected when
            # already nonzero" behavior (confirmed live), so it looks like
            # a general ChemDraw COM quirk treating a literal 0 write as a
            # no-op/unset sentinel rather than a real assignment. A negative
            # value DOES get accepted and clamps to 0 (confirmed: -1 -> 0)
            # -- but going through it is NOT safe: confirmed live that
            # `Isotope = -1` also corrupts the SAME atom's Charge to -1 as
            # a side effect (reproduced on a fresh, never-charged atom),
            # and the corrupted Charge could not then be fixed back to 0
            # either (same 0-from-nonzero rejection, recursively). Rather
            # than chase an increasingly risky workaround that trades one
            # data-corruption bug for another, this writes the requested
            # value plainly and reports the REAL post-write value (already
            # done below via a fresh `atom.Isotope` read) so a caller can
            # see honestly whether a reset actually landed, instead of a
            # workaround silently claiming success while corrupting charge.
            atom.Isotope = edit.get("isotope", 0)
        return {
            "id": targets.ensure_id(unit),
            "atom_index": idx,
            "ref": targets.atom_ref(atom),
            "element": t.element_symbol(atom.ElementNumber),
            "charge": atom.Charge,
            "isotope": atom.Isotope or None,
            "color": t.colorref_value_to_rgb_hex(atom.Color),
            "highlighted": bool(atom.Highlighted),
            "warning": atom.ChemicalWarning or None,
        }

    def edit_atom(self, target, atom_index, element=None, charge=None,
                  isotope=None, color=None, highlighted=None):
        edit = {"target": target, "atom": atom_index}
        if element is not None:
            edit["element"] = element
        if charge is not None:
            edit["set_charge"] = True
            edit["charge"] = charge
        if isotope is not None:
            edit["set_isotope"] = True
            edit["isotope"] = isotope
        if color is not None:
            edit["color"] = color
        if highlighted is not None:
            edit["highlighted"] = highlighted

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
        "bond_order"?, "color"?}. color: see _apply_atom_edit's docstring
        -- same '#RRGGBB' hex format, same base IChemDrawObject property,
        confirmed live to recolor the bond's drawn LINE (not just a
        label, since a bond has no label text). highlighted: see
        _apply_atom_edit's docstring -- real, round-trips cleanly, but
        NOT confirmed to be ChemDraw's actual "Highlight Color" GUI tool
        (an earlier claim here was wrong, corrected after the user
        directly compared the output to the real thing)."""
        unit = targets.resolve(doc, edit["target"], cache)[0]
        bond, idx = targets.resolve_bond(doc, unit, edit["bond"], cache)
        if edit.get("bond_order"):
            bond.BondOrder = t.bond_order_value(edit["bond_order"])
        if edit.get("color") is not None:
            bond.Color = t.rgb_hex_to_colorref(edit["color"])
        if edit.get("highlighted") is not None:
            bond.Highlighted = bool(edit["highlighted"])
        return {
            "id": targets.ensure_id(unit),
            "bond_index": idx,
            "ref": targets.bond_ref(bond),
            "bond_order": t.bond_order_name(bond.BondOrder),
            "color": t.colorref_value_to_rgb_hex(bond.Color),
            "highlighted": bool(bond.Highlighted),
            "warning": bond.ChemicalWarning or None,
        }

    def edit_bond(self, target, bond_index, bond_order=None, color=None,
                  highlighted=None):
        edit = {"target": target, "bond": bond_index}
        if bond_order is not None:
            edit["bond_order"] = bond_order
        if color is not None:
            edit["color"] = color
        if highlighted is not None:
            edit["highlighted"] = highlighted

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
