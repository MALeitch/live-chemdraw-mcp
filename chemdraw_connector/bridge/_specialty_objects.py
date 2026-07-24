"""Native ChemDraw "specialty" objects beyond plain annotations: polymer
brackets today, TLC plates/other Phase B+ objects share this file later.

Brackets share the same doc.Brackets/targets.iter_annotations/
find_annotation_by_id addressing _annotations.py's Arrows/Symbols already
use -- see targets.ANNOTATION_COLLECTIONS["bracket"].

KEY LIVE-PROBED SURPRISE (2026-07-21), not predictable from reflection
alone: one IChemDrawBracket is ONE bracket glyph (a single line/curve with
hook end-caps) -- NOT a full enclosing "[...]" pair the way the original
plan assumed. A real repeat-unit notation needs TWO Bracket objects (an
"opening" and a "closing" mark), which is why make_bracket below always
creates a pair. See its docstring for the rest of what was confirmed live:
Start/End are plain points (atom objects are rejected), BracketUsage drives
a real but non-overridable auto-label, and RepeatCount/SRULabel/
ComponentOrder are COM-broken (both get and put reliably raise), and
InsideAtoms/OutsideAtoms/ContainedAtoms/CrossingBonds do NOT reliably
reflect true geometric containment -- confirmed inconsistent across
multiple controlled tests, so none of that membership data is trusted or
exposed here.

SECOND SURPRISE, genuinely NOT resolved this session (documented honestly
rather than papered over): a single Bracket's hook (end-cap) direction is
driven by which point is Start vs End (a top-to-bottom vector renders hooks
curling right/"["; bottom-to-top curls left/"]") -- confirmed reliably on a
LONE bracket, and PolymerFlipType also flips a lone bracket reliably. BUT
this stops being reliable once a SECOND Bracket object is created in the
same COM automation session: across many controlled live tests, the
second-created bracket sometimes renders with its own correct independent
orientation and sometimes gets visually "stuck" matching the first one's
orientation regardless of its own Start/End vector OR PolymerFlipType --
neither reversing Start/End, toggling PolymerFlipType, nor swapping
creation order made this deterministic. No root cause was found (this is
presumably an internal ChemDraw rendering-cache quirk specific to having
multiple Brackets touched within one automation connection, since two
brackets created via two SEPARATE process reattachments did mirror
correctly). Practical effect: chemdraw_make_bracket's opening/closing pair
is coded with the semantically-correct opposite vectors, and often renders
as a proper mirrored "[...]" pair, but is NOT guaranteed to -- always
screenshot (chemdraw_export_image) after calling it and be ready to fix
the closing bracket's orientation by hand in the ChemDraw UI if it came out
matching the opening one instead of mirrored.

TLC PLATES (2026-07-21), added by a later agent to this same file/mixin --
doc.MakeTLCPlate() confirmed to add to doc.TLCPlates exactly like
Brackets/Arrows/Symbols add to their own collections (see
targets.ANNOTATION_COLLECTIONS["tlc_plate"]); doc.Objects.Clear() (what
use_scratch_document calls) confirmed to clear doc.TLCPlates too, so no
special-case scratch-clearing code was needed (unlike doc.Graphics, which
needed its own doc.Graphics.Clear() call -- see _document_session.py).

GOOD NEWS, resolves the plan's central open question: IChemDrawTLCSpot has
NO Position/Bounds/Top/Bottom/Left/Right properties at all (confirmed via
AttributeError probing every base-object property Bracket/Arrow/Symbol
all have) -- a spot's rendered position is driven ENTIRELY by its Rf
property, linearly interpolated by ChemDraw itself between the plate's
OriginFraction and SolventFrontFraction lines. Confirmed live via a 5-spot,
3-lane screenshot: measured pixel positions for rf=0.2, 0.35, 0.5, 0.8, 0.9
landed within ~1% of their expected fraction between the origin and
solvent-front dashed lines, and lanes rendered as evenly spaced columns
across the plate width automatically. NO domain/tlc_layout.py module was
built -- it would have had zero real work to do, per the plan's own "don't
build speculatively" instruction.

THREE SEPARATE, CONFIRMED-LIVE COM BUGS in AddLane/AddSpot, none
predictable from reflection (which showed only a bare "retval" arg with no
visible input params for either method):

1. Both AddLane(retval) and AddSpot(retval) reject a true zero-arg call
   ("Parameter not optional", a pywintypes.com_error) despite reflection
   showing no real input parameter -- a deep GetFuncDesc/GetRefTypeInfo
   dump (not just the name-only pass) showed the "retval" arg is actually
   typed as IChemDrawTLCLane**/IChemDrawTLCSpot** with IN|OUT flags but
   NOT the PARAMFLAG_FRETVAL bit that would let win32com treat it as a
   pure return value -- confirmed live that passing a plain `None`
   satisfies the call and the real new Lane/Spot object comes back as the
   normal return value anyway (the passed None is never actually
   consumed). make_tlc_plate below always calls plate.AddLane(None) /
   lane.AddSpot(None).

2. The Lane object literally returned by plate.AddLane(None) is
   confirmed UNRELIABLE for its own immediately-following .AddSpot() call
   -- reproduced deterministically: whichever Lane was most recently
   created (by creation order, not call order) raises a bare COM
   "Exception occurred" on .AddSpot() forever, via that specific object
   reference, even after touching unrelated objects in between. The fix,
   also confirmed live: re-fetch the SAME lane fresh via
   `plate.Lanes.Item(plate.Lanes.Count)` immediately after AddLane, and use
   THAT reference for AddSpot -- works immediately, every time, no retry
   loop needed. (Once a later sibling Lane is created, the earlier Lane's
   original stale reference spontaneously starts working too -- but don't
   rely on that, always refetch.)

3. A single Lane can hold AT MOST 2 spots via AddSpot -- confirmed as a
   hard, deterministic cap (not a staleness artifact: reproduced even with
   full plate/lane/spot refetching at every step). A 3rd AddSpot() call on
   a lane already at 2 spots is a SILENT NO-OP: Spots.Count simply never
   increases past 2, no exception, no signal of failure -- make_tlc_plate
   below explicitly checks Spots.Count after populating a lane and raises
   if a caller's spot list didn't fully land, rather than silently
   returning a plate with fewer spots than requested. Separately (and
   easy to trip by accident): calling AddSpot() twice in a row on the same
   lane WITHOUT writing any property to the first spot in between is
   ALSO a silent no-op for the second call (the first spot appears to stay
   "uncommitted" until some property write forces it real) -- so
   make_tlc_plate's spot-creation pass always writes Rf immediately after
   each AddSpot, before creating the next spot in the same lane.

FOURTH CONFIRMED BUG, the reason make_tlc_plate does a two-pass
lane-population (create-all-then-style-all) instead of setting each spot's
full style once, in place, as it's created: creating spot N+1 in a lane
SILENTLY RESETS spot N's Rf/Tail/Bold/Filled/Dashed back to ChemDraw's
defaults (confirmed live, deterministic, every time) -- ShowRf is the only
property observed to survive a sibling's creation. A property written
AFTER every spot in that lane already exists sticks permanently (confirmed:
re-setting spot 1 as the very last operation, after spot 2 already has its
final values, does not disturb spot 2 either) -- so every spot's full
style must be (re-)applied in one final pass per lane, after that lane's
last AddSpot call, never interleaved with more AddSpot calls on the same
lane. This reset is scoped to siblings within the SAME lane only --
confirmed live that creating/styling spots in a different lane never
disturbs an already-finalized lane.

NOT resolved this session, reported rather than guessed: IChemDrawTLCSpot's
Tail property is actually a float (default 0.0; setting Tail=True reads
back as -1.0, not True/1.0 -- it likely accepts an explicit tail-length
value rather than being a true boolean, but that wasn't explored beyond
confirming the True/False round trip at the Python-bool level). Whether
Tail renders as a visible comet-tail smear could NOT be visually confirmed
-- the available chemdraw_export_image resolution was too coarse to
distinguish a tailed spot from a plain filled dot at the DPI values tried
(200 and 600 -- the 600 DPI request did not appear to actually increase the
exported pixel dimensions). Filled=False + Dashed=True + Bold=True WAS
visually confirmed distinct (renders as a hollow, dashed-outline circle,
screenshot-verified) -- only Tail's visual effect remains unconfirmed.
ShowSideTicks was confirmed to default to False and stayed False in every
probe; the dashed line with "+" tick marks visible near the bottom of
scratch-document screenshots is ChemDraw's own page/print-area guide, NOT
a TLC plate property -- do not mistake it for ShowSideTicks output."""
from .. import targets
from ..com import types as t
from ..errors import ChemDrawError, InvalidInputError
from ._plumbing import SLOW_TIMEOUT


class _SpecialtyObjects:
    def _bracket_rect(self, doc, target, cache, left, top, right, bottom):
        """Resolve the rectangle to bracket: explicit left/top/right/bottom
        win if all four are given; otherwise the union bounding box of
        `target`'s resolved units (Left/Top/Right/Bottom, the same plain
        properties _split_wrapper_groups already reads elsewhere in this
        codebase)."""
        if None not in (left, top, right, bottom):
            return float(left), float(top), float(right), float(bottom)
        if not target:
            raise InvalidInputError(
                "make_bracket needs either a target structure to wrap, or "
                "all four of left/top/right/bottom explicitly."
            )
        units = targets.resolve(doc, target, cache)
        if not units:
            raise InvalidInputError(f"No structure resolved for target={target!r}")
        l = min(u.Left for u in units)
        t_ = min(u.Top for u in units)
        r = max(u.Right for u in units)
        b = max(u.Bottom for u in units)
        return l, t_, r, b

    def make_bracket(self, target, bracket_type="square", bracket_usage="sru",
                     left=None, top=None, right=None, bottom=None,
                     margin=10.0, polymer_repeat_pattern=None):
        """Wrap a structure (or an explicit rectangle) with a matching PAIR
        of bracket marks -- an opening "[" and a closing "]" (or curly/round
        equivalent) -- e.g. the standard "[...]n" polymer repeat-unit
        notation.

        target: structure to wrap (its bounding box + `margin` on every
        side defines the rectangle), or "" if left/top/right/bottom are all
        given explicitly (document points, top-left origin) instead.

        bracket_type: square | curly | round -- confirmed live to render as
        visually distinct glyphs (straight hooked line / curly brace with a
        center bulge / big parenthesis-like arc).

        bracket_usage: sru | monomer | mer | copolymer |
        copolymer_alternating | copolymer_random | copolymer_block |
        crosslink | graft | modification | component | mixture_unordered |
        mixture_ordered | multiple_group | generic | anypolymer |
        unspecified. Confirmed live: ChemDraw renders an automatic
        abbreviation label next to the CLOSING bracket based on this value
        (sru -> "n", monomer -> "mon", crosslink -> "xl", unspecified -> no
        label) -- this is ChemDraw's own generated text, not something this
        connector writes. The OPENING bracket is always created with
        bracket_usage=unspecified (no label), matching real ChemDraw
        convention where only the closing bracket carries the subscript.

        KNOWN COM LIMITATION (confirmed live, not solved): RepeatCount,
        SRULabel, and ComponentOrder all raise a COM exception on BOTH get
        and put, regardless of bracket_usage -- so the label text/count
        cannot be customized (e.g. there is no way to get "n+1" or a
        specific repeat count via COM), only the fixed auto-generated
        abbreviation above. Not exposed as parameters here since setting
        them does not work.

        KNOWN LIMITATION (confirmed live, not solved): the read-only
        InsideAtoms/OutsideAtoms/ContainedAtoms/CrossingBonds properties do
        NOT reliably reflect which atoms/bonds this bracket geometrically
        encloses -- confirmed inconsistent across several controlled tests
        (an atom clearly outside the requested rectangle still appeared in
        ContainedAtoms). Treat a bracket as a purely DECORATIVE pair of
        glyphs positioned near a structure, not as something that carries
        real chemical membership -- same class of gap as
        chemdraw_make_arrow/chemdraw_make_symbol (won't move if the
        wrapped structure is moved later; doc.Brackets is also a separate
        collection from doc.Groups, so a bracket never shows up in
        target="document"/"selection" structure counts either).

        margin: added on every side of target's bounding box (ignored if
        left/top/right/bottom are given explicitly).

        polymer_repeat_pattern: head_to_tail | head_to_head |
        either_unknown, or None to leave ChemDraw's default. Confirmed
        settable live (get/put round-trips) but no distinguishable visual
        effect was found this session -- treat as experimental.

        KNOWN LIMITATION, NOT resolved this session (confirmed live across
        many tests): the hook (end-cap) direction is driven by which point
        is Start vs End (a top-to-bottom vector renders hooks curling
        right/"["; bottom-to-top curls left/"]"), and this method sets the
        opening bracket to top-to-bottom and the closing one to
        bottom-to-top accordingly -- the semantically correct setup for a
        mirrored "[...]" pair. This reliably produces a correct mirror on a
        LONE bracket, but is NOT reliably respected once a second Bracket
        object exists in the same automation session -- confirmed live that
        the second-created bracket sometimes renders with its own correct
        orientation and sometimes visually "sticks" to match the first
        one's orientation instead, regardless of its own Start/End vector
        or PolymerFlipType (also tried, also inconsistent; no combination
        tested made this deterministic). ALWAYS screenshot the result
        (chemdraw_export_image) to check whether the pair actually mirrored
        -- if not, fix the closing bracket's orientation by hand in the
        ChemDraw UI."""
        type_val = t.bracket_type_value(bracket_type)
        usage_val = t.bracket_usage_value(bracket_usage)
        pattern_val = (t.polymer_repeat_pattern_value(polymer_repeat_pattern)
                      if polymer_repeat_pattern is not None else None)

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            l, top_, r, b = self._bracket_rect(
                doc, target, cache, left, top, right, bottom)
            l -= margin
            top_ -= margin
            r += margin
            b += margin
            backup = self._maybe_snapshot(doc)

            opening = doc.MakeBracket(type_val)
            sp = opening.Start
            sp.X, sp.Y = l, top_
            opening.Start = sp
            ep = opening.End
            ep.X, ep.Y = l, b
            opening.End = ep
            opening.BracketUsage = t.bracket_usage_value("unspecified")
            if pattern_val is not None:
                opening.PolymerRepeatPattern = pattern_val

            closing = doc.MakeBracket(type_val)
            sp2 = closing.Start
            sp2.X, sp2.Y = r, b
            closing.Start = sp2
            ep2 = closing.End
            ep2.X, ep2.Y = r, top_
            closing.End = ep2
            closing.BracketUsage = usage_val
            if pattern_val is not None:
                closing.PolymerRepeatPattern = pattern_val

            open_id = targets.ensure_id(opening)
            close_id = targets.ensure_id(closing)
            return {
                "opening_object_id": open_id,
                "closing_object_id": close_id,
                "bracket_type": t.bracket_type_name(opening.BracketType),
                "bracket_usage": t.bracket_usage_name(closing.BracketUsage),
                "rect": {"left": round(l, 2), "top": round(top_, 2),
                        "right": round(r, 2), "bottom": round(b, 2)},
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def set_bracket_style(self, object_id, bracket_type=None, bracket_usage=None,
                          polymer_repeat_pattern=None):
        """Edit one existing bracket glyph (object_id from chemdraw_make_bracket's
        opening_object_id/closing_object_id, or chemdraw_list_brackets) --
        note a pair created by chemdraw_make_bracket is two independent
        objects, so restyling "the bracket" as a matched pair means calling
        this twice, once per id. Only params explicitly passed (not None)
        are changed."""
        type_val = (t.bracket_type_value(bracket_type)
                   if bracket_type is not None else None)
        usage_val = (t.bracket_usage_value(bracket_usage)
                    if bracket_usage is not None else None)
        pattern_val = (t.polymer_repeat_pattern_value(polymer_repeat_pattern)
                      if polymer_repeat_pattern is not None else None)

        def go():
            doc = self._doc()
            br = targets.find_annotation_by_id(doc, "bracket", object_id)
            backup = self._maybe_snapshot(doc)
            if type_val is not None:
                br.BracketType = type_val
            if usage_val is not None:
                br.BracketUsage = usage_val
            if pattern_val is not None:
                br.PolymerRepeatPattern = pattern_val
            return {
                "object_id": object_id,
                "bracket_type": t.bracket_type_name(br.BracketType),
                "bracket_usage": t.bracket_usage_name(br.BracketUsage),
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def list_brackets(self, target="document"):
        """Enumerate every bracket glyph in the document (brackets aren't
        scoped to a structure, so `target` is currently always the whole
        document) -- each entry is ONE glyph (an opening or closing mark),
        not a matched pair; see chemdraw_make_bracket's docstring."""
        def go():
            doc = self._doc()
            out = []
            for br in targets.iter_annotations(doc, "bracket"):
                out.append({
                    "object_id": targets.ensure_id(br),
                    "start": {"x": round(br.Start.X, 2), "y": round(br.Start.Y, 2)},
                    "end": {"x": round(br.End.X, 2), "y": round(br.End.Y, 2)},
                    "bracket_type": t.bracket_type_name(br.BracketType),
                    "bracket_usage": t.bracket_usage_name(br.BracketUsage),
                })
            return {"brackets": out}
        return self._run(go)

    MAX_SPOTS_PER_LANE = 2  # hard COM cap, confirmed live -- see module docstring

    def _validate_tlc_lanes(self, lanes):
        for li, spots in enumerate(lanes):
            if len(spots) > self.MAX_SPOTS_PER_LANE:
                raise InvalidInputError(
                    f"lane {li}: {len(spots)} spots requested, but a "
                    f"single TLC lane can hold at most "
                    f"{self.MAX_SPOTS_PER_LANE} spots -- confirmed live, a "
                    "further AddSpot() call on the same lane is a silent "
                    "no-op (Spots.Count never increases, no exception "
                    "raised). Split extra spots across additional lanes."
                )
            for si, spot in enumerate(spots):
                rf = spot.get("rf")
                try:
                    rf = float(rf)
                except (TypeError, ValueError):
                    raise InvalidInputError(
                        f"lane {li} spot {si}: rf must be a number, got "
                        f"{spot.get('rf')!r}"
                    ) from None
                if not 0.0 <= rf <= 1.0:
                    raise InvalidInputError(
                        f"lane {li} spot {si}: rf must be in [0, 1], got {rf!r}"
                    )

    def make_tlc_plate(self, left, top, right, bottom, lanes,
                       origin_fraction=None, solvent_front_fraction=None,
                       show_origin=None, show_solvent_front=None,
                       show_borders=None, show_side_ticks=None,
                       transparent=None):
        """Draw a native TLC (thin-layer chromatography) plate: a
        rectangular plate outline with `len(lanes)` vertical lanes, each
        containing labeled Rf spots -- for reaction-monitoring figures.

        left/top/right/bottom: the plate's rectangle in document points
        (72/inch, top-left origin). Unlike chemdraw_make_bracket, there is
        no "wrap a structure" mode here -- a TLC plate is a standalone
        illustration of a physical plate, not something drawn around
        existing chemistry, so all four coordinates are required.

        lanes: a list of lists, outer list = lanes left-to-right, inner
        list = that lane's spots (confirmed live: lanes render as evenly
        spaced columns automatically, no manual x-position needed). Each
        spot is a dict: {"rf": 0.0-1.0 (required), "width":, "height":,
        "tail": bool, "filled": bool, "bold": bool, "dashed": bool,
        "show_rf": bool}. CONFIRMED LIVE: a spot's vertical position is
        driven entirely by "rf" (ChemDraw interpolates it between the
        plate's origin and solvent-front lines) -- there is no position
        property on a spot at all, so no manual Rf-to-coordinate math is
        needed or possible here.

        HARD LIMIT, confirmed live: each lane can hold AT MOST 2 spots --
        a 3rd spot in the same lane's list raises InvalidInputError before
        any COM calls happen (ChemDraw itself would otherwise silently
        drop it with no error at all; see this module's docstring for how
        that was confirmed).

        "tail" (comet-tail smear, common for real non-sharp TLC spots):
        settable and confirmed to change the underlying property, but its
        VISUAL effect could not be confirmed at available screenshot
        resolution -- treat as experimental, screenshot at high zoom on a
        single spot before relying on it for a real figure.

        "filled"=False combined with "dashed"=True IS confirmed visually
        distinct (renders as a hollow, dashed-outline circle vs. a plain
        solid dot).

        "show_rf": prints ChemDraw's own auto-generated "Rf = 0.NN" text
        label next to that spot (confirmed live).

        origin_fraction/solvent_front_fraction: 0-1 fractional position of
        the baseline/solvent-front lines along the plate's length (both
        default to 0.1 if not given, i.e. inset 10% from each end).
        show_origin/show_solvent_front/show_borders/show_side_ticks/
        transparent: plate-level display toggles, all optional (leave
        ChemDraw's own defaults if not passed).

        KNOWN LIMITATION, same class as chemdraw_make_arrow/make_symbol/
        make_bracket: this is a free-floating annotation, not bound to any
        chemistry -- doc.TLCPlates is a separate collection from
        doc.Groups, so a TLC plate never appears in
        target="document"/"selection" structure counts, and won't move if
        nearby structures are moved later."""
        lanes = lanes or []
        self._validate_tlc_lanes(lanes)
        l, top_, r, b = float(left), float(top), float(right), float(bottom)

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            plate = doc.MakeTLCPlate()
            tl = plate.TopLeft; tl.X, tl.Y = l, top_; plate.TopLeft = tl
            tr = plate.TopRight; tr.X, tr.Y = r, top_; plate.TopRight = tr
            bl = plate.BottomLeft; bl.X, bl.Y = l, b; plate.BottomLeft = bl
            br = plate.BottomRight; br.X, br.Y = r, b; plate.BottomRight = br

            if origin_fraction is not None:
                plate.OriginFraction = float(origin_fraction)
            if solvent_front_fraction is not None:
                plate.SolventFrontFraction = float(solvent_front_fraction)
            if show_origin is not None:
                plate.ShowOrigin = bool(show_origin)
            if show_solvent_front is not None:
                plate.ShowSolventFront = bool(show_solvent_front)
            if show_borders is not None:
                plate.ShowBorders = bool(show_borders)
            if show_side_ticks is not None:
                plate.ShowSideTicks = bool(show_side_ticks)
            if transparent is not None:
                plate.Transparent = bool(transparent)

            lane_results = []
            for spots in lanes:
                # AddLane(None): the "retval" arg is required despite
                # reflection showing no real input param (see module
                # docstring, finding #1) -- None satisfies it and is
                # never actually consumed.
                plate.AddLane(None)
                # REQUIRED refetch (finding #2): the Lane object AddLane
                # itself returns is unreliable for its own .AddSpot() --
                # plate.Lanes.Item(...) gives a working reference.
                lane = plate.Lanes.Item(plate.Lanes.Count)

                # Pass 1: create every spot in this lane, writing Rf
                # immediately after each AddSpot (finding #3's second
                # half -- an AddSpot with no property write on the
                # previous spot is itself a silent no-op).
                for spot in spots:
                    lane.AddSpot(None)
                    lane.Spots.Item(lane.Spots.Count).Rf = float(spot["rf"])

                if lane.Spots.Count != len(spots):
                    raise ChemDrawError(
                        f"Requested {len(spots)} spot(s) on this lane but "
                        f"ChemDraw only registered {lane.Spots.Count} -- "
                        "see this module's docstring on AddSpot's silent "
                        "no-op behavior; this should not happen given the "
                        "write-Rf-immediately pattern used here, so report "
                        "this as a new/different COM quirk if it recurs."
                    )

                # Pass 2: re-apply every property on every spot in this
                # lane, only now that the lane's full spot count exists
                # (finding #4 -- creating spot N+1 resets spot N's style
                # back to ChemDraw's defaults; only a final pass sticks).
                for i, spot in enumerate(spots, start=1):
                    s = lane.Spots.Item(i)
                    s.Rf = float(spot["rf"])
                    if "width" in spot:
                        s.Width = float(spot["width"])
                    if "height" in spot:
                        s.Height = float(spot["height"])
                    if spot.get("tail"):
                        s.Tail = True
                    if "filled" in spot:
                        s.Filled = bool(spot["filled"])
                    if spot.get("bold"):
                        s.Bold = True
                    if spot.get("dashed"):
                        s.Dashed = True
                    if spot.get("show_rf"):
                        s.ShowRf = True

                lane_results.append({
                    "spot_count": lane.Spots.Count,
                    "rf_values": [round(lane.Spots.Item(i).Rf, 4)
                                 for i in range(1, lane.Spots.Count + 1)],
                })

            plate_id = targets.ensure_id(plate)
            return {
                "object_id": plate_id,
                "lane_count": plate.Lanes.Count,
                "lanes": lane_results,
                "origin_fraction": round(plate.OriginFraction, 4),
                "solvent_front_fraction": round(plate.SolventFrontFraction, 4),
                "rect": {"left": round(l, 2), "top": round(top_, 2),
                        "right": round(r, 2), "bottom": round(b, 2)},
                "backup_path": backup,
            }
        # Scale beyond SLOW_TIMEOUT for plates with many lanes/spots --
        # never loop a mutating COM call across many items inside one
        # fixed timeout (see README's wedge-detector note).
        total_spots = sum(len(spots) for spots in lanes)
        timeout = max(SLOW_TIMEOUT, 3.0 * (len(lanes) + total_spots) + 10.0)
        return self._run(go, timeout=timeout)

    def set_tlc_plate_style(self, object_id, origin_fraction=None,
                            solvent_front_fraction=None, show_origin=None,
                            show_solvent_front=None, show_borders=None,
                            show_side_ticks=None, transparent=None):
        """Edit an existing TLC plate's display properties (object_id
        from chemdraw_make_tlc_plate or chemdraw_list_tlc_plates). Only
        params explicitly passed (not None) are changed. Does not touch
        lanes/spots -- rebuild via chemdraw_make_tlc_plate for structural
        changes (lane/spot count, Rf values)."""
        def go():
            doc = self._doc()
            plate = targets.find_annotation_by_id(doc, "tlc_plate", object_id)
            backup = self._maybe_snapshot(doc)
            if origin_fraction is not None:
                plate.OriginFraction = float(origin_fraction)
            if solvent_front_fraction is not None:
                plate.SolventFrontFraction = float(solvent_front_fraction)
            if show_origin is not None:
                plate.ShowOrigin = bool(show_origin)
            if show_solvent_front is not None:
                plate.ShowSolventFront = bool(show_solvent_front)
            if show_borders is not None:
                plate.ShowBorders = bool(show_borders)
            if show_side_ticks is not None:
                plate.ShowSideTicks = bool(show_side_ticks)
            if transparent is not None:
                plate.Transparent = bool(transparent)
            return {
                "object_id": object_id,
                "origin_fraction": round(plate.OriginFraction, 4),
                "solvent_front_fraction": round(plate.SolventFrontFraction, 4),
                "show_origin": plate.ShowOrigin,
                "show_solvent_front": plate.ShowSolventFront,
                "show_borders": plate.ShowBorders,
                "show_side_ticks": plate.ShowSideTicks,
                "transparent": plate.Transparent,
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    def list_tlc_plates(self, target="document"):
        """Enumerate every TLC plate in the document (plates aren't
        scoped to a structure, so `target` is currently always the whole
        document), each with its lane count and every lane's spot Rf
        values."""
        def go():
            doc = self._doc()
            out = []
            for plate in targets.iter_annotations(doc, "tlc_plate"):
                lanes = []
                for i in range(1, plate.Lanes.Count + 1):
                    lane = plate.Lanes.Item(i)
                    lanes.append({
                        "spot_count": lane.Spots.Count,
                        "rf_values": [round(lane.Spots.Item(j).Rf, 4)
                                     for j in range(1, lane.Spots.Count + 1)],
                    })
                out.append({
                    "object_id": targets.ensure_id(plate),
                    "lane_count": plate.Lanes.Count,
                    "lanes": lanes,
                    "origin_fraction": round(plate.OriginFraction, 4),
                    "solvent_front_fraction": round(plate.SolventFrontFraction, 4),
                    "rect": {"left": round(plate.Left, 2), "top": round(plate.Top, 2),
                            "right": round(plate.Right, 2), "bottom": round(plate.Bottom, 2)},
                })
            return {"tlc_plates": out}
        return self._run(go)
