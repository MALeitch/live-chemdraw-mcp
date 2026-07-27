"""Reaction scheme layout: reactants + arrow + products."""
from .. import targets
from ..domain import layout_math
from ..domain.reagent_text import build_text_cdxml, split_subscript_runs
from ._plumbing import SLOW_TIMEOUT

_POSITION_TOLERANCE = 0.5  # points; see the verify-and-correct step below


class _Reaction:
    def make_reaction_scheme(self, reactants, products, reagents_text=None,
                             fmt="smiles", conditions_text=None,
                             anchor_y=None):
        """Insert -> plan -> move -> annotate, in that strict order (four
        separate passes, not interleaved). Confirmed live: an earlier
        version that inserted each structure and moved it immediately,
        with MakeArrow()/MakeCaption() calls for the other scheme elements
        happening in between, left later structures (reliably the
        products) sitting at their raw auto-placed position instead of the
        intended one -- the Move() call didn't reliably stick when other
        object creation followed it in the same call. Root cause not fully
        pinned down (a live-bounds read shortly after insertion appears to
        be the trigger, though a direct same-thread reproduction of the
        exact same sequence did NOT reproduce it, so a COM apartment/
        worker-thread timing difference is suspected but unconfirmed).
        Two independent, complementary fixes instead of one: (1) every
        structure is now inserted and MOVED before any arrow/caption is
        created, matching the phased insert-then-measure-then-move pattern
        arrange_grid/build_scope_table already use for the same reason;
        (2) each move is verified against a fresh bounds read afterward and
        corrected once if it didn't land, regardless of whatever the exact
        cause turns out to be -- the same "measure the real result, don't
        trust an assumption" pattern arrange_in_region's rotation handling
        already relies on elsewhere in this codebase.

        Also confirmed live: the reagents_text caption (and "+"/fallback
        "->" captions) used to bleed off to the right of the arrow instead
        of sitting centered over it -- Caption.Position.X sets the
        caption's LEFT edge, not its center (the same fact
        _add_caption_to_unit/fix_caption_gaps already correct for
        elsewhere in this file/module), so anchoring a long reagents string
        at the arrow's center-x pushed its whole width to the right of that
        point. Fixed by measuring each caption's real rendered width and
        centering on it, same pattern as _add_caption_to_unit. The arrow's
        reserved horizontal space also now grows with the reagents text
        instead of staying a fixed 70pt regardless of how long the
        conditions are -- borrowed directly from CleanRXN+'s (a separate,
        working ChemDraw add-in for reaction-scheme layout) auto-width
        rule: arrow space = max(default, reagent text width + padding).

        One more confirmed live: the arrow itself didn't grow with that
        reserved space either -- Arrow.Position is ALSO not a center (it
        aliases Arrow.End, one of its two endpoints), and MakeArrow()'s
        default Start/End are only ~29pt apart regardless of the layout
        computed above, so a long reagents string ended up centered over a
        gap the drawn arrow barely spanned. Fixed by setting Start/End
        explicitly to the full reserved width instead of just Position.

        Reagent formula subscripts (the "3" in PPh3, "2"/"3" in K2CO3) are
        real ChemDraw text formatting, not a Unicode-character substitute:
        Caption.Styles has no way to add a new formatted run to an
        EXISTING caption through COM (confirmed live, no Add method), but
        a caption built from CDXML text at insertion time can have
        multiple <s> runs, and a CDX text run's face="32" attribute is
        genuine subscript (font shrink + baseline shift, confirmed live) --
        so the reagents caption is now constructed as CDXML instead of a
        plain MakeCaption()+.Text= call. See domain/reagent_text.py for
        which digits get treated as formula subscripts.

        conditions_text: standard single-step-scheme convention (ACS
        style and similar) is reagents/catalysts ABOVE the arrow,
        solvent/temperature/time/other physical conditions (wavelength,
        microwave, pressure...) BELOW it -- reagents_text and
        conditions_text map directly onto that split; pass both for a
        two-line arrow annotation, or reagents_text alone for the
        original single-line-above behavior. Both go through the exact
        same subscript-run/measure-real-width/verify-and-correct
        machinery described above, and the arrow's reserved space grows
        to fit whichever of the two is wider, not just reagents_text.
        """
        reactants = [self._validate_input(r, fmt) for r in reactants]
        products = [self._validate_input(p, fmt) for p in products]

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            cache = self._cache_for(doc)
            if anchor_y is not None:
                y = anchor_y
            else:
                # Auto-append below existing content by default instead of
                # always drawing at the same fixed row (see layout_math.
                # next_scheme_anchor_y's own docstring for the confirmed
                # live bug this replaces). Cheap, cache-backed COM reads
                # only (targets.iter_units + Captions/Arrows collections)
                # -- NOT state.build_snapshot, which pays for a full CDXML
                # export already covered by _maybe_snapshot just above.
                existing_bottoms = []
                for u in targets.iter_units(doc, cache):
                    try:
                        existing_bottoms.append(u.Bottom)
                    except Exception:
                        continue
                for coll_name in ("Captions", "Arrows"):
                    coll = getattr(doc, coll_name, None)
                    if coll is None:
                        continue
                    for i in range(1, coll.Count + 1):
                        try:
                            existing_bottoms.append(coll.Item(i).Bottom)
                        except Exception:
                            continue
                y = layout_math.next_scheme_anchor_y(existing_bottoms)
            gap = 24.0
            plus_width = 18.0

            # Phase 1: insert every structure first. No Move()/MakeArrow()/
            # MakeCaption() call happens until every insertion is done.
            reactant_units = [self._insert_structure_units(doc, r, fmt)
                              for r in reactants]
            product_units = [self._insert_structure_units(doc, p, fmt)
                             for p in products]

            # Phase 1b: create the reagents-text AND conditions-text
            # captions now (if given), purely to measure their real
            # rendered widths up front -- needed both to center them later
            # (see docstring) and to size the arrow's reserved space
            # around them (CleanRXN+'s auto-width rule, now driven by
            # whichever of the two is wider). Reused in phase 4 rather
            # than recreated. Built as CDXML (see docstring) so
            # formula-subscript digits get real ChemDraw text formatting,
            # not a Unicode-character substitute. Falls back to a plain
            # caption if that insertion doesn't take for any reason -- the
            # text must never just silently disappear.
            def _make_annotation_caption(text):
                runs = split_subscript_runs(text)
                cdxml = build_text_cdxml(runs, x=0, y=0, width=500, height=14)
                caps_before = doc.Captions.Count
                # Confirmed live 2026-07-23: when `text` ALONE (the whole
                # string, not a substring) parses as a chemical name/
                # formula ChemDraw recognizes -- bare "NaH" or "PCC", but
                # NOT "NaH (1.10 equiv)" -- this CDXML text-object insert
                # silently ALSO creates a real one-atom phantom structure
                # elsewhere on the page, as an unwanted side effect of
                # whatever auto-recognition ChemDraw's CDXML importer runs
                # on <t> content. A caption-only insert must never create
                # real chemistry, so snapshot every addressable unit before
                # the insert and delete anything new after it -- reuses
                # targets.iter_units/ensure_id/unit_objects(...).Clear(),
                # the exact same lookup+delete path chemdraw_remove already
                # uses for a real structure, rather than reinventing
                # atom/group bookkeeping here. cache=None forces a fresh
                # scan on both sides so a phantom created between them is
                # never masked by a stale doc_signature match.
                before_ids = {targets.ensure_id(u)
                             for u in targets.iter_units(doc, None)}
                try:
                    self._insert_raw(doc.Objects, "text/xml", cdxml)
                except Exception:
                    pass
                if doc.Captions.Count > caps_before:
                    cap = doc.Captions.Item(doc.Captions.Count)
                else:
                    cap = doc.MakeCaption()
                    cap.Text = text
                for u in targets.iter_units(doc, None):
                    if targets.ensure_id(u) not in before_ids:
                        try:
                            targets.unit_objects(u).Clear()
                        except Exception:
                            pass
                try:
                    width = cap.Right - cap.Left
                except Exception:
                    width = 0.0
                # Describes the WHOLE reaction, not any one reactant/
                # product -- without this, canvas.associate_captions'
                # spatial fallback can (and did, confirmed live) match it
                # to whichever structure happens to sit nearest, mislabeling
                # it as that structure's own caption.
                try:
                    targets.tag_caption_owner(cap, targets.NO_CAPTION_OWNER)
                except Exception:
                    pass
                return cap, width

            reagents_cap = reagents_width = None
            if reagents_text:
                reagents_cap, reagents_width = _make_annotation_caption(reagents_text)
            conditions_cap = conditions_width = None
            if conditions_text:
                conditions_cap, conditions_width = _make_annotation_caption(conditions_text)
            arrow_len = layout_math.arrow_length_for_reagents(
                max(reagents_width or 0.0, conditions_width or 0.0))

            # Phase 2: plan every x position from each unit's width alone
            # (widths are stable right after insertion even though
            # position isn't -- confirmed live, every mis-positioned
            # structure still had the correct bounding-box size). Records
            # the "+" caption x's here too, so phase 4 places them from
            # this one plan instead of recomputing the same layout twice.
            # The actual position math is pure (domain/layout_math.py,
            # unit-tested there) -- this just feeds it widths and zips the
            # result back onto the live COM units.
            reactant_widths = [[u.Right - u.Left for u in units]
                               for units in reactant_units]
            product_widths = [[u.Right - u.Left for u in units]
                              for units in product_units]
            (reactant_x, product_x, plus_positions,
             arrow_left_x) = layout_math.plan_reaction_layout(
                reactant_widths, product_widths, arrow_len,
                start_x=60.0, gap=gap, plus_width=plus_width)

            structure_plan = []  # (unit, target_left)
            for units, group_x in zip(reactant_units, reactant_x):
                structure_plan.extend(zip(units, group_x))
            for units, group_x in zip(product_units, product_x):
                structure_plan.extend(zip(units, group_x))

            # Phase 3: move every structure to its planned position, then
            # verify the move actually landed -- re-reading live bounds
            # and issuing one corrective move if it didn't, rather than
            # trusting a single Move() call always sticks.
            ids = []
            placed_units = []  # (unit, id), paired directly -- reused below
            # for the overlap check instead of re-deriving the pairing
            for u, target_left in structure_plan:
                objs = targets.unit_objects(u)
                objs.Move(target_left - u.Left, y - (u.Top + u.Bottom) / 2.0)
                actual_center = (u.Top + u.Bottom) / 2.0
                if (abs(u.Left - target_left) > _POSITION_TOLERANCE
                        or abs(actual_center - y) > _POSITION_TOLERANCE):
                    objs.Move(target_left - u.Left, y - actual_center)
                uid = targets.ensure_id(u)
                ids.append(uid)
                placed_units.append((u, uid))

            # Compute actual "+" positions from the REAL structure positions
            # after phase 3 moves, not from the phase 2 plan.
            # Reconstruct the original group structure from placed_units.
            reactant_group_sizes = [len(g) for g in reactant_units]
            product_group_sizes = [len(g) for g in product_units]

            # Split placed_units into reactant and product groups
            total_reactant_units = sum(reactant_group_sizes)
            reactant_placed_flat = [u for u, _ in placed_units[:total_reactant_units]]
            product_placed_flat = [u for u, _ in placed_units[total_reactant_units:]]

            # Reconstruct groups
            reactant_placed_groups = []
            idx = 0
            for size in reactant_group_sizes:
                reactant_placed_groups.append(reactant_placed_flat[idx:idx + size])
                idx += size

            product_placed_groups = []
            idx = 0
            for size in product_group_sizes:
                product_placed_groups.append(product_placed_flat[idx:idx + size])
                idx += size

            actual_plus_positions = []
            # Reactant "+" positions (between adjacent reactant groups)
            for i in range(len(reactant_placed_groups) - 1):
                prev_right = max(u.Right for u in reactant_placed_groups[i])
                curr_left = min(u.Left for u in reactant_placed_groups[i + 1])
                mid = (prev_right + curr_left) / 2.0
                actual_plus_positions.append(mid)

            # Product "+" positions (between adjacent product groups)
            for i in range(len(product_placed_groups) - 1):
                prev_right = max(u.Right for u in product_placed_groups[i])
                curr_left = min(u.Left for u in product_placed_groups[i + 1])
                mid = (prev_right + curr_left) / 2.0
                actual_plus_positions.append(mid)

            # Phase 4: arrow, "+"s, and reagents text -- placed last, from
            # the plan built in phase 2, now that every structure's final
            # position is locked in. Every caption here is centered on its
            # own measured width, not just anchored at a raw point (see
            # docstring: Position.X is the caption's LEFT edge).
            #
            # Every Position set here is verified and corrected once if it
            # didn't land -- the same "measure the real result, don't trust
            # a single call" pattern phase 3 already applies to structure
            # Move(). Added after a live incident where a reagents-text
            # caption ended up ~140pt away from its intended position with
            # no error raised; the two live repros run immediately
            # afterward against a clean single-client ChemDraw session both
            # landed correctly on the first try, so the likely cause was
            # cross-client contention (multiple automation clients hitting
            # the same shared scratch document at once -- see README's
            # contention note) rather than a deterministic bug in this
            # positioning logic. This verify-and-correct doesn't depend on
            # knowing that root cause: it catches and self-heals any
            # single-call placement failure, whatever produces it, the same
            # way phase 3 already does for structures. If a placement still
            # doesn't land after the retry, it's reported in
            # violations.mislaid_captions rather than silently trusted.
            mislaid = []

            def _place_and_verify(obj, target_x, target_y):
                self._set_position(obj, target_x, target_y)
                landed = _at(obj, target_x, target_y)
                if not landed:
                    self._set_position(obj, target_x, target_y)
                    landed = _at(obj, target_x, target_y)
                return landed

            def _at(obj, target_x, target_y):
                try:
                    return (abs(obj.Position.X - target_x) <= _POSITION_TOLERANCE
                            and abs(obj.Position.Y - target_y) <= _POSITION_TOLERANCE)
                except Exception:
                    return False

            def place_centered_caption(label, text, center_x, center_y):
                cap = doc.MakeCaption()
                cap.Text = text
                try:
                    w = cap.Right - cap.Left
                except Exception:
                    w = 0.0
                if not _place_and_verify(cap, center_x - w / 2.0, center_y):
                    mislaid.append(label)
                # A "+" or the arrow-fallback "->" glyph, not a label for
                # either structure it sits between -- see reagents_cap's
                # tagging above for why an untagged decoration caption is
                # unsafe (spatial fallback can mislabel it as whichever
                # structure happens to sit nearest).
                try:
                    targets.tag_caption_owner(cap, targets.NO_CAPTION_OWNER)
                except Exception:
                    pass
                return cap

            decorations = []  # (label, obj) for every non-structure element,
            # gathered so the overlap check below covers the WHOLE scheme --
            # a caption or the arrow colliding with something is just as
            # "not clean" as two structures colliding.

            # Compute actual "+" positions from the REAL structure positions after phase 3
            # placed_units contains (unit, uid) tuples in the order they were placed
            # reactant_units and product_units were used for planning, but placed_units
            # has the actual moved positions. We need to extract the actual positions.
            reactant_placed = [u for u, _ in placed_units[:sum(len(u) for u in reactant_units)]]
            product_placed = [u for u, _ in placed_units[len(reactant_placed):]]
            
            # Group them back into their original groups
            # reactant_units is a list of groups, each group is a list of units
            # We need to reconstruct the grouping from placed_units
            reactant_placed_groups = []
            idx = 0
            for group in reactant_units:
                group_size = len(group)
                actual_group = []
                for _ in range(group_size):
                    actual_group.append(placed_units[idx][0])
                    idx += 1
                reactant_placed_groups.append(actual_group)
            
            product_placed_groups = []
            for group in product_units:
                group_size = len(group)
                actual_group = []
                for _ in range(group_size):
                    actual_group.append(placed_units[idx][0])
                    idx += 1
                product_placed_groups.append(actual_group)
            
            actual_plus_positions = []
            for i in range(len(reactant_placed_groups) - 1):
                right_edge = max(u.Right for u in reactant_placed_groups[i])
                left_edge = min(u.Left for u in reactant_placed_groups[i + 1])
                mid = (right_edge + left_edge) / 2.0
                actual_plus_positions.append(mid)

            for i in range(len(product_placed_groups) - 1):
                right_edge = max(u.Right for u in product_placed_groups[i])
                left_edge = min(u.Left for u in product_placed_groups[i + 1])
                mid = (right_edge + left_edge) / 2.0
                actual_plus_positions.append(mid)

            for i, mid_x in enumerate(actual_plus_positions):
                label = f"+{i}"
                decorations.append(
                    (label, place_centered_caption(label, "+", mid_x, y)))
            arrow_ok = False
            arrow_id = None
            try:
                arrow = doc.MakeArrow()
                # Confirmed live: Position (like Caption.Position) is NOT
                # the arrow's center -- it's one of its two endpoints
                # (Position == End), and MakeArrow()'s default Start/End
                # are only ~29pt apart regardless of the reserved space
                # computed above. Setting Position alone left a short,
                # fixed-length arrow sitting off-center under a much wider
                # reagents caption instead of spanning the full gap. Set
                # both endpoints explicitly instead: default Start.X >
                # End.X (Start is the head/right side, End the tail/left
                # side, confirmed live), so End gets the left edge and
                # Start the right edge to preserve the arrowhead pointing
                # right, toward the products, exactly as MakeArrow() draws
                # it by default.
                arrow_center_x = arrow_left_x + arrow_len / 2.0
                end_pt = arrow.End
                end_pt.X, end_pt.Y = arrow_left_x, y
                arrow.End = end_pt
                start_pt = arrow.Start
                start_pt.X, start_pt.Y = arrow_left_x + arrow_len, y
                arrow.Start = start_pt
                arrow_ok = True
                # Tagged (like every structure unit) so a plain scheme
                # arrow can be found again afterward via
                # chemdraw_set_arrow_style/chemdraw_list_arrows (targets.
                # ensure_id works on any IChemDrawObject, already proven
                # live on Caption -- see targets.tag_caption_owner).
                arrow_id = targets.ensure_id(arrow)
                decorations.append(("arrow", arrow))
            except Exception:
                arrow_center_x = arrow_left_x + arrow_len / 2.0
                decorations.append(
                    ("arrow", place_centered_caption("arrow", "→", arrow_center_x, y)))

            if reagents_cap is not None:
                try:
                    w = reagents_cap.Right - reagents_cap.Left
                except Exception:
                    w = reagents_width
                if not _place_and_verify(reagents_cap, arrow_center_x - w / 2.0,
                                         y - 24.0):
                    mislaid.append("reagents_text")
                decorations.append(("reagents_text", reagents_cap))

            if conditions_cap is not None:
                try:
                    w = conditions_cap.Right - conditions_cap.Left
                except Exception:
                    w = conditions_width
                # Mirrors reagents_text's y - 24.0 on the other side of the
                # arrow (standard single-step-scheme convention: reagents
                # above, conditions -- solvent/temp/time/special conditions
                # like wavelength or microwave -- below).
                if not _place_and_verify(conditions_cap, arrow_center_x - w / 2.0,
                                         y + 24.0):
                    mislaid.append("conditions_text")
                decorations.append(("conditions_text", conditions_cap))

            # Verify the whole scheme, not just trust the layout math: read
            # every placed element's FINAL bounds (structures, arrow, every
            # caption) and flag any pair that actually overlaps, rather than
            # assuming the math above always produces a clean result. Same
            # "measure the real result" philosophy as the position
            # verify-and-correct in phase 3, and reuses the exact overlap
            # check chemdraw_describe_canvas already relies on
            # (layout_math.find_overlaps) instead of a bespoke one here.
            boxes, labels = [], []
            for u, uid in placed_units:
                boxes.append(layout_math.Box(u.Left, u.Top, u.Right, u.Bottom))
                labels.append(uid)
            for label, obj in decorations:
                try:
                    boxes.append(layout_math.Box(obj.Left, obj.Top, obj.Right, obj.Bottom))
                    labels.append(label)
                except Exception:
                    continue
            overlaps = layout_math.find_overlaps(boxes, ids=labels)
            off_page = layout_math.page_overflow(
                boxes, labels,
                float(doc.Width or 540.0), float(doc.Height or 720.0))

            return {
                "object_ids": ids,
                "arrow_native": arrow_ok,
                "arrow_object_id": arrow_id,
                "violations": {"overlapping": [list(p) for p in overlaps],
                                "mislaid_captions": mislaid,
                                "off_page": off_page},
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)
