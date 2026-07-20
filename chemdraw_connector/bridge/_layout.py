"""Publication layout: grids, scope tables, autonumbering, captions, the
semantic canvas read, region-constrained arrangement, and raw move/plan
tools."""
import os

from .. import snapshots, state, targets
from ..domain import canvas, diff, layout_math, numbering
from ..errors import InvalidInputError
from ._plumbing import SLOW_TIMEOUT


class _Layout:
    def _add_caption_to_unit(self, doc, unit, text, center_x, y, bold=False):
        """center_x is the desired horizontal CENTER of the caption, not
        Position.X directly — confirmed live, Caption.Position.X sets the
        caption's LEFT edge, so placing it at a structure's center-x
        without correcting for the caption's own width shifts long
        captions well off-center. Measure the caption's real rendered
        width right after setting its text (before positioning) and
        subtract half of it.

        The caption is tagged with targets.tag_caption_owner right after
        creation — this is the mechanism canvas.associate_captions actually
        relies on to find this caption's structure again later (see that
        function's docstring, tier 0), because the `cap.Group = unit` call
        below is a confirmed no-op."""
        cap = doc.MakeCaption()
        cap.Text = text
        if bold:
            try:
                cap.Face = 1
            except Exception:
                pass
        try:
            cap_w = cap.Right - cap.Left
        except Exception:
            cap_w = 0.0
        self._set_position(cap, center_x - cap_w / 2.0, y)
        try:
            targets.tag_caption_owner(cap, targets.ensure_id(unit))
        except Exception:
            pass
        try:
            # KNOWN NO-OP, kept anyway: confirmed live (test_bridge.py) that
            # this assignment raises nothing but reading cap.Group back
            # afterward is always None on ChemDraw 26 (Professional
            # 26.0.0.6141) — it never actually merges the caption into the
            # structure's group. Left in place because it's a single
            # try/excepted COM call (cheap) and costs nothing if a future
            # ChemDraw version fixes the underlying bug, it starts working
            # for free and reinforces the tag above rather than conflicting
            # with it. The tag set just above is what this connector
            # actually depends on today.
            cap.Group = unit
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
                   bold=True, group_sizes=None):
        """scheme='numeric-letter' requires group_sizes (e.g. [1, 3, 2] ->
        1, 2a, 2b, 2c, 3a, 3b), sized against `target` resolved in reading
        order (top-to-bottom, left-to-right — the same order the caller
        should have seen from chemdraw_get_layout/describe_canvas before
        deciding which structures share a number). Same "caller decides,
        this does not guess" shape as arrange_in_region's rotate_ids/
        flip_ids — there's no way to infer which structures are meant to
        share a group number from geometry alone."""
        def go():
            doc = self._doc()
            units = targets.resolve(doc, target, self._cache_for(doc))
            # Reading order: top-to-bottom rows, then left-to-right.
            units.sort(key=lambda u: (round(u.Top / 40.0), u.Left))
            labels = numbering.make_labels(len(units), start=start, scheme=scheme,
                                           group_sizes=group_sizes)
            for unit, label in zip(units, labels):
                cx = (unit.Left + unit.Right) / 2.0
                self._add_caption_to_unit(
                    doc, unit, label, cx, unit.Bottom + 12.0, bold=bold)
            return {
                "numbered": [
                    {"id": targets.ensure_id(u), "label": lbl}
                    for u, lbl in zip(units, labels)
                ]
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    @staticmethod
    def _gather_captions(doc):
        """Every caption on the page as (plain entries, live COM objects) —
        parallel lists, so callers can reason over the plain data and still
        reach the COM object to move it. Worker thread only.

        tag_owner_id is read from targets.get_caption_owner (the
        claude_caption_owner object tag _add_caption_to_unit stamps on
        every caption this connector creates) — canvas.associate_captions
        prefers it over group_id, since group_id is only ever populated for
        captions the USER grouped by hand in ChemDraw's own UI (that path
        isn't broken, only this connector's own `cap.Group = unit` call
        is)."""
        entries, objs = [], []
        for i in range(1, doc.Captions.Count + 1):
            c = doc.Captions.Item(i)
            try:
                grp = c.Group
                group_id = targets.ensure_id(grp) if grp is not None else None
            except Exception:
                group_id = None
            try:
                tag_owner_id = targets.get_caption_owner(c)
            except Exception:
                tag_owner_id = None
            entries.append({
                "text": c.Text or "",
                "bounds": {"left": round(c.Left, 1), "top": round(c.Top, 1),
                          "right": round(c.Right, 1), "bottom": round(c.Bottom, 1)},
                "group_id": group_id,
                "tag_owner_id": tag_owner_id,
            })
            objs.append(c)
        return entries, objs

    @staticmethod
    def _graphics_boxes(doc):
        """Panel rectangles (doc.Graphics) as plain dicts. Worker thread only."""
        boxes = []
        for i in range(1, doc.Graphics.Count + 1):
            g = doc.Graphics.Item(i)
            boxes.append({
                "index": i,
                "bounds": {"left": round(g.Left, 1), "top": round(g.Top, 1),
                          "right": round(g.Right, 1), "bottom": round(g.Bottom, 1)},
            })
        return boxes

    def _correct_caption_positions(self, cap_entries, cap_objs, owner_ids,
                                   deltas):
        """After moving structures by `deltas` ({object_id: (dx, dy)}),
        bring each caption they own along — but only by the residual its
        structure's move didn't already apply. A caption grouped with its
        structure is carried automatically by the group's Move (moving it
        again would double-move it, the exact failure move_objects' own
        docstring records for counterions); an ungrouped label sitting
        below its structure is not carried and needs the full delta. The
        residual, measured against the caption's pre-move bounds, handles
        both without needing to know which case applies.

        Returns (carried, corrected) counts. Worker thread only."""
        carried = corrected = 0
        for entry, cap, oid in zip(cap_entries, cap_objs, owner_ids):
            d = deltas.get(oid)
            if d is None:
                continue
            try:
                rdx = d[0] - (cap.Left - entry["bounds"]["left"])
                rdy = d[1] - (cap.Top - entry["bounds"]["top"])
                if abs(rdx) < 1.0 and abs(rdy) < 1.0:
                    carried += 1
                    continue
                pos = cap.Position
                if self._set_position(cap, pos.X + rdx, pos.Y + rdy):
                    corrected += 1
            except Exception:
                continue
        return carried, corrected

    def describe_canvas(self, region=None):
        """The one-call semantic read of the page: every unit classified
        (real structures vs zero-atom caption-wrapper groups), captions
        matched to the structures they label, panel boxes with their member
        structures, and violations (overlaps, box overflows) — the full
        picture a "read/interpret/organize" request needs, computed in one
        COM pass plus pure Python (domain/canvas.py).

        region: None for the whole page, {"box_index": N} to scope to one
        panel box, or an explicit {"left","top","right","bottom"} rect.
        """
        def go():
            doc = self._doc()
            units = state.build_snapshot(doc, self._cache_for(doc))
            cap_entries, _ = self._gather_captions(doc)
            boxes = self._graphics_boxes(doc)
            rect = None
            if region is not None:
                try:
                    rect = canvas.resolve_region(region, boxes)
                except ValueError as exc:
                    raise InvalidInputError(str(exc)) from exc
            out = canvas.build_canvas(units, cap_entries, boxes, region=rect)
            if rect is not None:
                out["region"] = rect
            return out
        return self._run(go, timeout=SLOW_TIMEOUT)

    DEFAULT_CANVAS_EXPORT_NAME = "chemdraw-mcp-canvas-export.csv"

    def export_canvas_table(self, path=None, fmt="csv"):
        """Write the full classified canvas inventory (every real
        structure, caption, panel box, and excluded wrapper/decoration
        unit — everything describe_canvas computes) to one CSV file
        instead of returning it inline. describe_canvas on a large,
        unscoped page can exceed the inline tool-result size limit; this
        has no such ceiling since the caller pages through the file
        instead. path: defaults to one well-known, overwritten-each-call
        location (same convention as the scratch document) so exports
        don't accumulate."""
        if fmt != "csv":
            raise InvalidInputError("Only csv is supported")

        def go():
            doc = self._doc()
            units = state.build_snapshot(doc, self._cache_for(doc))
            cap_entries, _ = self._gather_captions(doc)
            boxes = self._graphics_boxes(doc)
            built = canvas.build_canvas(units, cap_entries, boxes)
            rows = canvas.canvas_to_rows(built)
            out_path = path or os.path.join(
                os.path.dirname(snapshots.BACKUP_DIR),
                self.DEFAULT_CANVAS_EXPORT_NAME)
            abspath = self._write_csv_rows(rows, out_path)
            counts = {"structures": len(built["structures"]),
                      "captions": len(built["captions"]),
                      "boxes": len(built["boxes"]),
                      "excluded": len(built["non_structure_units"])}
            return {"path": abspath, "row_counts": counts,
                    "total_rows": sum(counts.values())}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def arrange_in_region(self, region, object_ids, strategy="vertical_flow",
                          margin=6.0, align="center", h_gap=8.0, v_gap=8.0,
                          rotate_ids=None, flip_ids=None):
        """Fit structures inside a region (panel box or explicit rect) in
        ONE call: each structure's footprint is unioned with its own
        captions (found the same way describe_canvas finds them), target
        positions come from pure math (layout_math.distribute_vertical or
        shelf_pack for strategy="grid"), every move lands in one batch, and
        captions ride along. Nothing is ever rescaled — a layout that
        doesn't fit is REPORTED in `violations`, not forced. Items are
        placed in object_ids order, so reorder the list to reorder the
        column/grid. rotate_ids/flip_ids (subsets of object_ids) are
        rotated 90deg / mirrored BEFORE packing, so a tall item can be
        turned to fit a wide-but-short region — the caller decides which
        ids need it (e.g. from chemdraw_get_layout), this does not guess.
        WARNING (confirmed live): flipping a chiral structure can silently
        invert its stereocenter's CIP descriptor — ChemDraw mirrors the
        depiction but does not re-derive wedge/hash geometry to compensate.
        Any flip_ids id whose stereochemistry changed is reported in
        `violations.stereo_changed` — always check it before trusting a
        flip on real data."""
        if strategy not in ("vertical_flow", "grid"):
            raise InvalidInputError(
                f"strategy must be 'vertical_flow' or 'grid', got {strategy!r}")

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            cache = self._cache_for(doc)
            before = state.build_snapshot(doc, self._cache_for(doc))
            before_by_id = {s["id"]: s for s in before}
            cap_entries, cap_objs = self._gather_captions(doc)
            boxes = self._graphics_boxes(doc)
            try:
                rect = canvas.resolve_region(region, boxes)
            except ValueError as exc:
                raise InvalidInputError(str(exc)) from exc
            real, wrapper_map, others = canvas.classify_units(before)
            owner_ids = canvas.associate_captions(
                real, cap_entries, wrapper_map, {u["id"] for u in others},
                boxes)

            by_id = {targets.ensure_id(u): u
                     for u in targets.iter_units(doc, cache)}

            stereo_changed = []
            if rotate_ids or flip_ids:
                for oid in (rotate_ids or []):
                    unit = by_id.get(oid)
                    if unit is not None:
                        self._apply_transform_action(
                            targets.unit_objects(unit), "rotate",
                            0.0, 0.0, 90.0, 1.0, False)
                for oid in (flip_ids or []):
                    unit = by_id.get(oid)
                    if unit is None:
                        continue
                    stereo_before = self._atom_cip_descriptors(doc, unit, cache)
                    self._apply_transform_action(
                        targets.unit_objects(unit), "flip",
                        0.0, 0.0, 0.0, 1.0, False)
                    # Flip mirrors the depiction but ChemDraw does not
                    # re-derive wedge/hash geometry, so a stereocenter's
                    # CIP descriptor can silently invert (confirmed live:
                    # S -> R on a flipped alanine) — detect and report it
                    # rather than trust the mirror is chemistry-neutral.
                    stereo_after = self._atom_cip_descriptors(doc, unit, cache)
                    if stereo_before != stereo_after:
                        stereo_changed.append(oid)
                # Re-read live bounds rather than assume rotation swapped
                # w/h — same reasoning as every other geometry read here:
                # measure the real result instead of trusting a pivot we
                # haven't pinned down.
                live = state.build_snapshot(doc, self._cache_for(doc))
                live_by_id = {s["id"]: s for s in live}
            else:
                live_by_id = before_by_id

            picked, comps, missing = [], [], []
            for oid in object_ids:
                unit = by_id.get(oid)
                snap = live_by_id.get(oid)
                if unit is None or snap is None or not snap.get("bounds"):
                    missing.append(oid)
                    continue
                comp = dict(snap["bounds"])
                for entry, owner in zip(cap_entries, owner_ids):
                    if owner == oid:
                        comp = canvas.union_bounds(comp, entry["bounds"])
                picked.append((oid, unit))
                comps.append(comp)
            if not picked:
                raise InvalidInputError(
                    f"none of object_ids resolved to a structure with bounds "
                    f"(missing: {missing})")

            container = layout_math.Box(**rect)
            sizes = [(c["right"] - c["left"], c["bottom"] - c["top"])
                     for c in comps]
            if strategy == "grid":
                inset = layout_math.Box(
                    container.left + margin, container.top + margin,
                    container.right - margin, container.bottom - margin)
                positions, overflow = layout_math.shelf_pack(
                    sizes, inset, h_gap=h_gap, v_gap=v_gap)
            else:
                positions, overflow = layout_math.distribute_vertical(
                    sizes, container, margin=margin, align=align)
            # Physically impossible fits only — margin-squeezed items are
            # already covered honestly by still_overflowing after the move.
            too_wide = [oid for (w, _), (oid, _u) in zip(sizes, picked)
                        if w > container.width]

            deltas = {}
            for (oid, unit), comp, (tx, ty) in zip(picked, comps, positions):
                dx, dy = tx - comp["left"], ty - comp["top"]
                targets.unit_objects(unit).Move(dx, dy)
                deltas[oid] = (dx, dy)
            carried, corrected = self._correct_caption_positions(
                cap_entries, cap_objs, owner_ids, deltas)

            after = state.build_snapshot(doc, self._cache_for(doc))
            d = diff.diff_snapshots(before, after)
            unexpected = [m for m in d["moved"] if m["id"] not in deltas]
            after_by_id = {s["id"]: s for s in after}
            resulting = {oid: after_by_id.get(oid, {}).get("bounds")
                         for oid in deltas}
            still = [{"id": oid, "edges": canvas.overflow_edges(b, rect)}
                     for oid, b in resulting.items()
                     if b and canvas.overflow_edges(b, rect)]
            return {
                "arranged": [{"object_id": oid,
                              "dx": round(dx, 1), "dy": round(dy, 1)}
                             for oid, (dx, dy) in deltas.items()],
                "missing": missing,
                "captions_carried": carried,
                "captions_corrected": corrected,
                "violations": {
                    "overflow_pt": overflow,
                    "too_wide": too_wide,
                    "still_overflowing": still,
                    "stereo_changed": stereo_changed,
                },
                "resulting_bounds": resulting,
                "unexpected_moves": unexpected,
                "rotated": rotate_ids or [],
                "flipped": flip_ids or [],
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=max(SLOW_TIMEOUT, 2.0 * len(object_ids)))

    @staticmethod
    def _group_caption_rows(owner_bounds):
        """Cluster {owner_id: bounds} into rows by Y-range overlap (one
        pass, sorted by top: a structure joins the current row if its top
        is above that row's accumulated bottom, else starts a new row).
        Returns {owner_id: row_bottom} — every structure in the same row
        maps to that row's shared (max) bottom, so their captions can align
        to one baseline instead of each sitting at its own structure's
        bottom."""
        ordered = sorted(owner_bounds.items(), key=lambda kv: kv[1]["top"])
        rows = []
        for owner_id, b in ordered:
            if rows and b["top"] < rows[-1]["bottom"]:
                rows[-1]["bottom"] = max(rows[-1]["bottom"], b["bottom"])
                rows[-1]["members"].append(owner_id)
            else:
                rows.append({"bottom": b["bottom"], "members": [owner_id]})
        return {oid: row["bottom"] for row in rows for oid in row["members"]}

    def fix_caption_gaps(self, object_ids=None, gap=12.0, pairs=None,
                         align_rows=True):
        """Snap each owned caption to sit truly centered (accounting for
        the caption's OWN rendered width, not just its structure's center)
        directly below its structure's CURRENT bounds. Every other layout
        tool (arrange_in_region, move_objects) only ever carries a caption
        by the same delta its structure moved, which preserves whatever
        offset the caption already had — including a bad one. This is the
        fix for that: it recomputes the offset from scratch instead of
        preserving it. object_ids=None fixes every real structure's owned
        caption.

        Default gap is 12.0, not the visually-implied 4.0: `Caption.Position`
        is NOT the caption's top-left corner (confirmed live — gap=4.0, the
        value autonumber used to use, still left ~5pt of visible overlap
        with the structure above it). Also confirmed live: Position.X is
        the caption's LEFT edge, not its center — setting it to the
        structure's center-x (without subtracting half the caption's own
        width) put every caption's left edge at its structure's center,
        shifting long captions well off-center. Fixed here by reading each
        caption's own rendered width from `_gather_captions` and
        subtracting half of it.

        align_rows: when True (default), structures whose Y-ranges overlap
        are treated as one visual row and ALL their captions align to that
        row's shared bottom, instead of each caption sitting at its own
        structure's bottom — otherwise captions in the same row look
        ragged when row members have different heights (common right after
        contract_group, since shorthand-collapsed structures vary a lot in
        size). Set False to anchor every caption to only its own structure.

        pairs: optional {structure_id: caption_text} exact mapping, for
        when a caption was moved independently of its structure (e.g.
        move_objects with move_with_captions=False) and now sits too far
        away for proximity-based association (canvas.associate_captions)
        to find the right owner — bypasses association, matches by exact
        caption text instead. Confirmed live this matters: association
        picked the nearest structure to each caption's STALE position,
        which was wrong once structures had moved elsewhere."""
        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            backup = self._maybe_snapshot(doc)
            snap = state.build_snapshot(doc, cache)
            snap_by_id = {s["id"]: s for s in snap}
            cap_entries, cap_objs = self._gather_captions(doc)

            if pairs:
                by_text = {}
                for entry, obj in zip(cap_entries, cap_objs):
                    by_text.setdefault(entry["text"], (entry, obj))
                targeted = []
                for oid, text in pairs.items():
                    found = by_text.get(text)
                    if oid in snap_by_id and snap_by_id[oid].get("bounds") and found is not None:
                        targeted.append((oid, found[0], found[1]))
            else:
                boxes = self._graphics_boxes(doc)
                real, wrapper_map, others = canvas.classify_units(snap)
                owner_ids = canvas.associate_captions(
                    real, cap_entries, wrapper_map,
                    {u["id"] for u in others}, boxes)
                wanted = set(object_ids) if object_ids else {u["id"] for u in real}
                targeted = [
                    (owner, entry, obj)
                    for entry, obj, owner in zip(cap_entries, cap_objs, owner_ids)
                    if owner in wanted and owner in snap_by_id
                    and snap_by_id[owner].get("bounds")
                ]

            row_bottom = {}
            if align_rows and targeted:
                owner_bounds = {oid: snap_by_id[oid]["bounds"]
                                for oid, _e, _o in targeted}
                row_bottom = self._group_caption_rows(owner_bounds)

            fixed = []
            for owner, entry, obj in targeted:
                b = snap_by_id[owner]["bounds"]
                cap_w = entry["bounds"]["right"] - entry["bounds"]["left"]
                cx = (b["left"] + b["right"]) / 2.0 - cap_w / 2.0
                cy = row_bottom.get(owner, b["bottom"]) + gap
                self._set_position(obj, cx, cy)
                fixed.append({"structure_id": owner, "caption": entry["text"]})
            return {"fixed": fixed, "backup_path": backup}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def get_layout(self, target="document"):
        """Full layout snapshot for planning a reorganization: every
        structure's id/formula/bounds (scoped to `target`), every caption's
        text and its raw ownership signals (group_id from Caption.Group,
        tag_owner_id from the claude_caption_owner tag — see
        canvas.associate_captions for how these are actually resolved to an
        owning structure), and every panel rectangle on the page
        (doc.Graphics) — all in ONE COM round trip. Captions and boxes are
        always returned for the whole page
        (not scoped to `target`) since planning a move needs the
        surrounding context regardless of which structures are the actual
        subject.

        Built after a first reorganization attempt needed five-plus
        separate probes (bounds, then captions, then box rectangles, then
        group ownership discovered ad hoc) to assemble this same picture —
        the fix is gathering it all up front, once, so a plan can be
        computed entirely offline before any ChemDraw call is made.
        describe_canvas layers classification/relationships on top of this
        same data; prefer it for interpretation, this for raw geometry.
        """
        def go():
            doc = self._doc()
            w, h = float(doc.Width or 540.0), float(doc.Height or 720.0)
            if target == "document":
                structures = state.build_snapshot(doc, self._cache_for(doc))
            else:
                structures = [state.describe_unit(u, w, h)
                             for u in targets.resolve(doc, target, self._cache_for(doc))]
            captions, _ = self._gather_captions(doc)
            boxes = self._graphics_boxes(doc)
            return {"structures": structures, "captions": captions, "boxes": boxes}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def move_objects(self, moves, move_with_captions=True):
        """Apply many independent moves in ONE COM session, instead of one
        MCP tool call per object — the batch-execution half of a
        reorganization plan computed offline against chemdraw_get_layout's
        output.

        moves: [{"object_id": ..., "dx": ..., "dy": ...}, ...]

        move_with_captions: each moved structure's own captions (matched
        the same way describe_canvas matches them) ride along by the same
        delta, with the residual check in _correct_caption_positions
        preventing a double move when the group already carried them. Pass
        False to move structures only, leaving labels where they are.

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
            before = state.build_snapshot(doc, self._cache_for(doc))
            cap_entries = cap_objs = owner_ids = None
            if move_with_captions:
                cap_entries, cap_objs = self._gather_captions(doc)
                real, wrapper_map, others = canvas.classify_units(before)
                owner_ids = canvas.associate_captions(
                    real, cap_entries, wrapper_map, {u["id"] for u in others},
                    self._graphics_boxes(doc))

            # Resolve every target ONCE via a single document-wide scan,
            # not once per move (same lesson as contract_functional_groups'
            # earlier find_by_id-per-pass inefficiency).
            by_id = {targets.ensure_id(u): u for u in targets.iter_units(doc, self._cache_for(doc))}
            applied, missing, deltas = [], [], {}
            for mv in moves:
                oid = mv["object_id"]
                unit = by_id.get(oid)
                if unit is None:
                    missing.append(oid)
                    continue
                dx, dy = mv.get("dx", 0.0), mv.get("dy", 0.0)
                targets.unit_objects(unit).Move(dx, dy)
                applied.append(oid)
                deltas[oid] = (dx, dy)

            carried = corrected = 0
            if move_with_captions:
                carried, corrected = self._correct_caption_positions(
                    cap_entries, cap_objs, owner_ids, deltas)

            after = state.build_snapshot(doc, self._cache_for(doc))
            d = diff.diff_snapshots(before, after)
            requested_ids = {mv["object_id"] for mv in moves}
            unexpected = [m for m in d["moved"] if m["id"] not in requested_ids]
            after_by_id = {s["id"]: s for s in after}
            return {
                "applied": applied,
                "missing": missing,
                "captions_carried": carried,
                "captions_corrected": corrected,
                "resulting_bounds": {
                    oid: after_by_id.get(oid, {}).get("bounds")
                    for oid in applied
                },
                "unexpected_moves": unexpected,
                "diff": d,
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=max(SLOW_TIMEOUT, 2.0 * len(moves)))
