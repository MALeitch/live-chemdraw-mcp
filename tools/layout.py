"""Publication layout tools."""
from ._common import TARGET_DOC, as_json, with_preview
from .structure import _parse


def register(mcp, bridge):
    @mcp.tool(description=(
        "START HERE for any read/interpret/organize request — ONE call "
        "returns the whole canvas classified and related: real structures "
        "(id/formula/bounds/atom counts) each with its caption texts and "
        "the panel box that contains it, captions with the structure they "
        "label, boxes with their member structure ids, and violations "
        "(overlapping structures, structures overflowing their box). "
        "Zero-atom wrapper groups are listed separately under "
        "non_structure_units so they are never mistaken for molecules. "
        "region optionally scopes the result to one panel: "
        "{\"box_index\": N} or {\"left\":..,\"top\":..,\"right\":..,"
        "\"bottom\":..}. Prefer this over chemdraw_get_layout plus manual "
        "geometry — do not reconstruct these relationships yourself with "
        "multiple probing calls. ALWAYS check `violations.off_page` "
        "(structure ids sitting outside the document's real page, per "
        "`page_bounds`, and which edge(s) they cross) — a structure drawn "
        "past the page edge still renders fine in a preview image, since "
        "preview images auto-crop to whatever was drawn rather than "
        "showing the page boundary."))
    def chemdraw_describe_canvas(region: dict | None = None) -> str:
        return as_json(bridge.describe_canvas(region))

    @mcp.tool(description=(
        "Organize structures INTO a region (panel box or rect) in ONE call "
        "— use this instead of computing per-object dx/dy yourself for "
        "any \"fit/organize these in the box\" request. region: "
        "{\"box_index\": N} (from chemdraw_describe_canvas) or an explicit "
        "rect. object_ids: list of structure ids, placed in the "
        "given order (top-to-bottom for vertical_flow, reading order for "
        "grid). Each structure moves together with its own captions; "
        "structure sizes are NEVER changed — if the items cannot fit, the "
        "call still places them and reports it in `violations` "
        "(overflow_pt, too_wide, still_overflowing) for you to resolve "
        "(e.g. fold a branch with chemdraw_split_at_bond + "
        "chemdraw_transform, or ask the user). "
        "rotate_ids/flip_ids: lists of ids (subsets of "
        "object_ids) to turn 90deg / mirror BEFORE packing — use this when "
        "chemdraw_get_layout shows an item is too tall/wide for the "
        "target box as drawn. This executes the rotation/flip you specify, "
        "it does not decide for you: check bounds yourself first and pick "
        "the ids. Rotation is chemically safe (a rigid transform can't "
        "create/destroy stereocenters). Flip is NOT safe for chiral "
        "structures — confirmed live, it silently inverts a stereocenter's "
        "CIP descriptor (ChemDraw mirrors the depiction but doesn't "
        "re-derive wedge/hash geometry). Any flip_ids id whose "
        "stereochemistry changed is reported in `violations.stereo_changed` "
        "— ALWAYS check it, and undo/re-flip or fix the wedge manually if "
        "it's non-empty. ALWAYS check `violations` and `unexpected_moves`, "
        "and look at the preview image. A .cdxml backup is saved first "
        "(backup_path)."))
    def chemdraw_arrange_in_region(region: dict, object_ids: list[str],
                                   strategy: str = "vertical_flow",
                                   margin: float = 6.0, align: str = "center",
                                   h_gap: float = 8.0, v_gap: float = 8.0,
                                   rotate_ids: list[str] | None = None,
                                   flip_ids: list[str] | None = None):
        return with_preview(bridge.arrange_in_region(
            region, object_ids, strategy, margin, align, h_gap, v_gap,
            rotate_ids, flip_ids))

    @mcp.tool(description=(
        "Snap each structure's caption to sit truly centered (accounting "
        "for the caption's OWN width, not just its structure's center) "
        "directly below its CURRENT bounds (fixes captions overlapping or "
        "off-center from their structure). arrange_in_region and "
        "move_objects only ever carry a caption by the same delta its "
        "structure moved, which PRESERVES any pre-existing bad offset "
        "rather than fixing it — this recomputes the offset from scratch "
        "instead. object_ids: list of structure ids to fix "
        "(omit to fix every structure's owned caption, using "
        "proximity to find each one's owner). align_rows (default true): "
        "structures whose Y-ranges overlap are treated as one row and all "
        "their captions align to that row's shared bottom, instead of each "
        "sitting at its own structure's bottom — set false to anchor every "
        "caption independently. pairs: optional "
        "{\"structure_id\": \"caption text\"} exact mapping — use this "
        "INSTEAD of object_ids whenever a caption was moved "
        "independently of its structure (e.g. chemdraw_move_objects with "
        "move_with_captions=false) and is now too far from its true owner "
        "for proximity to find correctly; capture the correct id<->text "
        "pairs from chemdraw_describe_canvas BEFORE making any move that "
        "could separate them. Run this AFTER any bond-angle cleanup or "
        "shorthand contraction, since both can resize a structure and "
        "change where its caption should sit. Default gap=12.0: "
        "Caption.Position is NOT the caption's top-left corner, so a "
        "smaller gap can still leave visible overlap (confirmed live)."))
    def chemdraw_fix_caption_gaps(object_ids: list[str] | None = None,
                                  gap: float = 12.0,
                                  pairs: dict | None = None,
                                  align_rows: bool = True):
        return as_json(bridge.fix_caption_gaps(object_ids, gap, pairs, align_rows))

    @mcp.tool(description=(
        "Full layout snapshot for PLANNING a reorganization: every "
        "structure's id/formula/bounds, every caption's text and which "
        "structure it belongs to, and every panel rectangle on the page — "
        "all in one call. Use this FIRST for any \"move/rearrange these "
        "structures\" request, then compute the full target layout "
        "yourself (positions, which existing panel each item should land "
        "in, whether it fits within panel bounds) before calling "
        "chemdraw_move_objects — do not probe bounds one object at a time "
        "and move things incrementally; that is much slower and more "
        "error-prone than planning first, moving once. " + TARGET_DOC +
        " Captions and panel rectangles are always returned for the whole "
        "page regardless of target, since planning needs that context."))
    def chemdraw_get_layout(target: str = "document") -> str:
        return as_json(bridge.get_layout(_parse(target)))

    @mcp.tool(description=(
        "Apply many independent moves in ONE call instead of one call per "
        "object — the execution half of a plan built from "
        "chemdraw_get_layout's output. moves: list like "
        "[{\"object_id\": \"claude-...\", \"dx\": 12.0, \"dy\": -8.0}, ...] "
        "(points, 72/inch). Every call snapshots the document before and "
        "after and reports `unexpected_moves` — any object that moved "
        "WITHOUT being in your list (e.g. a counterion or satellite "
        "fragment that turned out to be carried along by a different "
        "object's move). ALWAYS check `unexpected_moves` in the result; if "
        "it's non-empty, something you didn't plan for shifted and needs "
        "a corrective move. Each structure's own captions move with it "
        "unless move_with_captions is false. The result reports "
        "`resulting_bounds` for every moved object — use those instead of "
        "a follow-up read to verify the layout. Also check "
        "`violations.off_page` — moving a structure can push it outside "
        "the document's actual page, which the preview image (auto-cropped "
        "to whatever was drawn) will NOT reveal. A .cdxml backup is saved "
        "first; the result includes backup_path and a preview image."))
    def chemdraw_move_objects(moves: list[dict], move_with_captions: bool = True):
        return with_preview(bridge.move_objects(moves, move_with_captions))

    @mcp.tool()
    def chemdraw_arrange_grid(items: list[dict], columns: int = 0,
                              layout: str = "double-column",
                              page_width_in: float = 0):
        """Arrange existing structures into a publication grid with optional
        labels beneath each. items: list like
        [{"object_id": "claude-...", "label": "3a, 85%"}, ...].
        layout: single-column (3.25 in) | double-column (6.5 in), or set
        page_width_in explicitly; columns=0 auto-fits. Note layout/
        page_width_in only controls column WRAPPING (a manuscript width),
        not the actual ChemDraw document page size — enough rows can still
        run past the bottom of the real page, which is what
        `violations.off_page` (structure/label ids and which edge(s) they
        cross) catches; it will NOT show up in the preview image, which
        auto-crops to whatever was drawn rather than the page boundary.

        The response includes a rendered preview image — LOOK AT IT and
        re-invoke with different columns/ordering if anything overlaps, labels
        wrap awkwardly, or the grid is unbalanced. ALWAYS also check
        `violations.off_page`. A document backup is saved
        first (backup_path) for rollback."""
        return with_preview(bridge.arrange_grid(
            items, columns or None, layout, page_width_in or None))

    @mcp.tool()
    def chemdraw_build_scope_table(entries: list[dict], columns: int = 0,
                                   layout: str = "double-column",
                                   page_width_in: float = 0):
        """Build an entire substrate-scope table in one call: insert every
        structure, place its label (name/yield) beneath it, and arrange the
        grid. entries: list like
        [{"representation": "c1ccccc1", "format": "smiles", "label": "1a, 92%"}, ...]
        (format defaults to smiles; also accepts name/molfile/inchi).

        A representation that inserts as more than one disconnected
        ChemDraw object (a salt like "[K+].[O-]C(=O)[O-].[K+]", or a
        name-resolved organometallic like "PdCl2(PPh3)2") still counts as
        ONE table entry/grid cell, with ONE label -- its fragments are kept
        together (moved as a rigid group, not each independently snapped
        to the cell) and carry ONE caption, tagged to the first fragment.
        object_ids reports that first fragment's id per entry; the full
        per-entry breakdown (every fragment's id, len 1 for an ordinary
        single-object entry) is in the parallel fragment_ids, for the rare
        case you need to address every ion/ligand individually afterward
        (e.g. chemdraw_select or chemdraw_transform on just one of them).

        The response includes a rendered preview image — LOOK AT IT and
        re-invoke or rearrange if anything is off. ALWAYS also check
        `violations.off_page`: enough entries can run the grid past the
        bottom of the document's actual page even though the preview image
        (auto-cropped to whatever was drawn) looks fine. A document backup
        is saved first (backup_path)."""
        return with_preview(bridge.build_scope_table(
            entries, columns or None, layout, page_width_in or None))

    @mcp.tool()
    def chemdraw_autonumber(target: str = "document", start: int = 1,
                            scheme: str = "numeric", bold: bool = True,
                            group_sizes: list[int] | None = None) -> str:
        """Stamp compound numbers (1, 2, 3...) beneath structures in reading
        order (top-to-bottom, left-to-right). scheme: numeric | numeric-letter.

        numeric-letter requires group_sizes, a list of how many
        consecutive structures (in that same reading order) share each
        number, e.g. [1, 3, 2] for 1, 2a, 2b, 2c, 3a, 3b. There is no way
        to infer this grouping automatically — call chemdraw_get_layout or
        chemdraw_describe_canvas first to see the structures in reading
        order and decide the grouping yourself."""
        return as_json(bridge.autonumber(
            _parse(target), start, scheme, bold, group_sizes))
