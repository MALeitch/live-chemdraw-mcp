"""Shorthand label contraction/expansion: whole-structure labels, bulk
label expansion, and functional-group detection/contraction (Ph, Boc, TBS...)."""
import pywintypes

from .. import targets
from ..com import types as t
from ..domain import cdxml_graph, label_filter, substructures
from ..errors import ChemDrawError
from ._plumbing import BULK_TIMEOUT, SLOW_TIMEOUT, _com_text


class _StaleHandleMap(Exception):
    """A cached COM proxy or doc-unique atom/bond id from an earlier
    _build_handle_map call turned out unusable for a later match in the same
    round — either a genuinely dead COM proxy, or the id space shifted
    underneath it. Caller rebuilds the map fresh and retries once. Internal
    to contract_functional_groups; never escapes to a tool caller."""


class _Shorthand:
    @staticmethod
    def _reresolve_after_mutation(doc, old_id, cache=None):
        """Contract/expand can rebuild a structure, orphaning its tag. If the
        old id still resolves, keep it; otherwise tag the newest untagged unit
        (the rebuilt structure) and report the id change.

        cache: threaded through to iter_units/find_by_id so this scan (run
        after EVERY unit contraction/expansion, and every round of
        _contract_round) populates the shared per-document cache instead of
        discarding its result — a plain uncached call here doesn't just cost
        a scan itself, it also means the NEXT round's own cached find_by_id
        gets a cache miss too, paying for the same scan twice."""
        try:
            targets.find_by_id(doc, old_id, cache)
            return old_id, None
        except Exception:
            untagged = [u for u in targets.iter_units(doc, cache)
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
            new_id, note = self._reresolve_after_mutation(doc, uid, self._cache_for(doc))
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
            new_id, note = self._reresolve_after_mutation(doc, uid, self._cache_for(doc))
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
            if contracted and current_uid is not None:
                # Collapsing atoms into a shorthand label can leave the
                # REMAINING drawn portion with distorted bond angles
                # (probed live: a benzyl's ring looked fine before
                # contraction, ugly after) — whole-unit clean is safe
                # (same as chemdraw_transform's, no sub-selection risk)
                # and current_uid doesn't change (verified live: clean
                # doesn't rebuild/retag a structure the way
                # ContractObjectsToLabel does).
                #
                # current_uid is checked for None first: _reresolve_after_
                # mutation can fail to re-identify a rebuilt structure
                # (already reported via `note` above), and _clean_unit(None)
                # would otherwise raise TargetNotFoundError uncaught here,
                # aborting every remaining unit in this batch instead of
                # just reporting this one as unclean. A live failure from
                # _clean_unit itself (structure changed again in between)
                # is caught the same way, per-unit, not batch-fatal —
                # matching edit_atoms/edit_bonds' per-item failure handling.
                try:
                    current_uid = self._run(
                        lambda uid=current_uid: self._clean_unit(uid),
                        timeout=SLOW_TIMEOUT)
                except Exception as exc:
                    note = (f"contracted {len(contracted)} group(s) but could "
                           f"not clean up the result afterward: {exc}")
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

    def _clean_unit(self, uid):
        """Whole-unit Clean Up Structure by id. Worker thread only."""
        doc = self._doc()
        unit = targets.find_by_id(doc, uid, self._cache_for(doc))
        self._apply_transform_action(
            targets.unit_objects(unit), "clean", 0.0, 0.0, 0.0, 1.0, False)
        return targets.ensure_id(unit)

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

        new_id, note = self._reresolve_after_mutation(doc, uid, self._cache_for(doc))
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
