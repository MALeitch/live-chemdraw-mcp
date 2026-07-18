"""Reaction scheme layout: reactants + arrow + products."""
from .. import targets
from ._plumbing import SLOW_TIMEOUT

_POSITION_TOLERANCE = 0.5  # points; see the verify-and-correct step below
_MIN_ARROW_LEN = 70.0
_ARROW_TEXT_PADDING = 20.0  # CleanRXN+'s auto-width margin around reagent text


class _Reaction:
    def make_reaction_scheme(self, reactants, products, reagents_text=None,
                             fmt="smiles"):
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
        """
        reactants = [self._validate_input(r, fmt) for r in reactants]
        products = [self._validate_input(p, fmt) for p in products]

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            y = 120.0
            gap = 24.0
            plus_width = 18.0

            # Phase 1: insert every structure first. No Move()/MakeArrow()/
            # MakeCaption() call happens until every insertion is done.
            reactant_units = [self._insert_structure_units(doc, r, fmt)
                              for r in reactants]
            product_units = [self._insert_structure_units(doc, p, fmt)
                             for p in products]

            # Phase 1b: create the reagents-text caption now (if any),
            # purely to measure its real rendered width up front -- needed
            # both to center it later (see docstring) and to size the
            # arrow's reserved space around it (CleanRXN+'s auto-width
            # rule). Reused in phase 4 rather than recreated.
            reagents_cap = None
            reagents_width = 0.0
            if reagents_text:
                reagents_cap = doc.MakeCaption()
                reagents_cap.Text = reagents_text
                try:
                    reagents_width = reagents_cap.Right - reagents_cap.Left
                except Exception:
                    reagents_width = 0.0
            arrow_len = max(_MIN_ARROW_LEN, reagents_width + _ARROW_TEXT_PADDING)

            # Phase 2: plan every x position from each unit's width alone
            # (widths are stable right after insertion even though
            # position isn't -- confirmed live, every mis-positioned
            # structure still had the correct bounding-box size). Records
            # the "+" caption x's here too, so phase 4 places them from
            # this one plan instead of recomputing the same layout twice.
            x = 60.0
            structure_plan = []  # (unit, target_left)
            plus_positions = []

            def lay_out_group(groups):
                nonlocal x
                for i, units in enumerate(groups):
                    if i:
                        plus_positions.append(x)
                        x += plus_width
                    for u in units:
                        w = u.Right - u.Left
                        structure_plan.append((u, x))
                        x += w + gap

            lay_out_group(reactant_units)
            arrow_left_x = x
            x += arrow_len + gap
            lay_out_group(product_units)

            # Phase 3: move every structure to its planned position, then
            # verify the move actually landed -- re-reading live bounds
            # and issuing one corrective move if it didn't, rather than
            # trusting a single Move() call always sticks.
            ids = []
            for u, target_left in structure_plan:
                objs = targets.unit_objects(u)
                objs.Move(target_left - u.Left, y - (u.Top + u.Bottom) / 2.0)
                actual_center = (u.Top + u.Bottom) / 2.0
                if (abs(u.Left - target_left) > _POSITION_TOLERANCE
                        or abs(actual_center - y) > _POSITION_TOLERANCE):
                    objs.Move(target_left - u.Left, y - actual_center)
                ids.append(targets.ensure_id(u))

            # Phase 4: arrow, "+"s, and reagents text -- placed last, from
            # the plan built in phase 2, now that every structure's final
            # position is locked in. Every caption here is centered on its
            # own measured width, not just anchored at a raw point (see
            # docstring: Position.X is the caption's LEFT edge).
            def place_centered_caption(text, center_x, center_y):
                cap = doc.MakeCaption()
                cap.Text = text
                try:
                    w = cap.Right - cap.Left
                except Exception:
                    w = 0.0
                self._set_position(cap, center_x - w / 2.0, center_y)
                return cap

            for px in plus_positions:
                place_centered_caption("+", px, y)

            arrow_center_x = arrow_left_x + arrow_len / 2.0
            arrow_ok = False
            try:
                arrow = doc.MakeArrow()
                self._set_position(arrow, arrow_center_x, y)
                arrow_ok = True
            except Exception:
                place_centered_caption("→", arrow_center_x, y)

            if reagents_cap is not None:
                try:
                    w = reagents_cap.Right - reagents_cap.Left
                except Exception:
                    w = reagents_width
                self._set_position(reagents_cap, arrow_center_x - w / 2.0, y - 24.0)

            return {
                "object_ids": ids,
                "arrow_native": arrow_ok,
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)
