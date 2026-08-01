"""End-to-end substrate-scope figure generation (RDKit enumeration ->
ChemDraw insertion/layout -> numbering -> QC), bundled into one call.

Precedent: chemdraw_build_scope_table (tools/layout.py) already bundles
insert+label+arrange for a caller who already HAS a list of structures.
This module goes one step earlier: given only a scaffold and a list of
R-group fragments, it replaces the 4-call chain

    chemdraw_enumerate_derivatives -> chemdraw_build_scope_table
    -> chemdraw_autonumber -> chemdraw_check_warnings

with one tool call, matching the common ask "make me a scope table from
these R-groups.\""""
from collections import defaultdict, deque

from chemdraw_connector.domain import layout_math

from ._common import TARGET_DOC, as_json, with_preview
from .structure import _parse


def _auto_label(index, row):
    """Sensible default caption text for one scope-table entry when the
    caller didn't supply `labels`: a running index plus whatever chemistry
    identity is actually available in this row -- formula and/or MW if
    either was among the requested `properties` (chemdraw_enumerate_
    derivatives defaults to mw+formula, so this is the common case), else
    the substituent's own SMILES identity, else just the bare index (the
    documented fallback when nothing else is available)."""
    parts = [str(index)]
    if row.get("formula"):
        parts.append(row["formula"])
    mw = row.get("mw")
    if isinstance(mw, (int, float)):
        parts.append(f"MW {mw:.1f}")
    if len(parts) == 1 and row.get("substituent"):
        parts.append(f"R = {row['substituent']}")
    return ", ".join(parts)


def _map_to_original_indices(substituents, rows):
    """chemdraw_enumerate_derivatives partitions `substituents` into
    successful `rows` and `failed` entries while preserving relative
    order, but drops the original index once a substituent fails -- to
    honor a caller's `labels` list (documented as positional against
    `substituents`, not against the possibly-shorter `rows`), each row
    needs to be matched back to the index it came from.

    Done with one FIFO queue per distinct substituent TEXT rather than a
    simple running counter, so duplicate substituent strings (e.g. the
    same fragment listed twice) are each consumed in first-seen order
    instead of both collapsing onto one index. This is safe because
    success/failure and the computed properties for a given substituent
    string depend only on that string + the scaffold (a pure function),
    never on its position -- so which of several identical occurrences
    maps to which duplicate row is interchangeable."""
    pending = defaultdict(deque)
    for i, sub in enumerate(substituents):
        pending[sub].append(i)
    indices = []
    for row in rows:
        q = pending.get(row["substituent"])
        indices.append(q.popleft() if q else None)
    return indices


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_generate_scope_figure(
        substituents: list[str],
        scaffold: str = "",
        format: str = "smiles",
        properties: list[str] | None = None,
        labels: list[str] | None = None,
        columns: int = 0,
        layout: str = "double-column",
        page_width_in: float = 0,
        autonumber_target: str = "document",
        autonumber_start: int = 1,
        autonumber_scheme: str = "numeric",
        autonumber_bold: bool = True,
        autonumber_group_sizes: list[int] | None = None,
        check_warnings_target: str = "document",
    ):
        """Build an entire substrate-scope FIGURE from a scaffold + R-group
        list in one call: enumerate every derivative (RDKit), insert +
        caption + grid-arrange them all, stamp compound numbers under the
        new grid, and run ChemDraw's chemical-warning QC pass -- the
        4-call chain (chemdraw_enumerate_derivatives ->
        chemdraw_build_scope_table -> chemdraw_autonumber ->
        chemdraw_check_warnings) collapsed into one, for the common
        "make me a scope table from these R-groups" ask.

        scaffold/format/properties are passed straight through to
        chemdraw_enumerate_derivatives (see its docstring): scaffold is
        SMILES/molfile containing one [*] attachment point (omit to read
        the current ChemDraw selection); properties defaults to
        mw+formula; substituents is capped at 500 per call there.

        Per-entry labels: each successfully-enumerated derivative becomes
        one chemdraw_build_scope_table entry (representation=its SMILES,
        format="smiles"). Its `label` caption is, in order of preference:
        (1) `labels[i]` if you passed `labels` -- a list the same length
        as `substituents`, positional against `substituents` itself (NOT
        against the shorter list of entries that actually got drawn --
        see below); (2) otherwise an auto-built label combining a running
        index with whatever chemistry identity is available: formula
        and/or MW if either was computed (the default), else the
        substituent's own SMILES, else the bare index alone.

        A substituent that fails RDKit fusion/sanitization (bad SMILES,
        chemically invalid fusion, etc.) is skipped -- it gets NO grid
        cell, and if you supplied `labels`, that substituent's label is
        simply never used (the rest of `labels` still lines up correctly
        against the substituents that DID succeed, since the mapping is
        positional against the original `substituents` list, not against
        the surviving entries). Every failure is still reported back in
        `failed_substituents` (from chemdraw_enumerate_derivatives'
        `failed`) so you know what to fix. If every substituent fails,
        nothing is drawn at all and the result says so plainly instead of
        calling build_scope_table with zero entries.

        columns/layout/page_width_in are passed straight through to
        chemdraw_build_scope_table (see its docstring) -- e.g. layout:
        single-column (3.25 in) | double-column (6.5 in) manuscript width,
        or set page_width_in explicitly; columns=0 auto-fits.

        autonumber_* controls the chemdraw_autonumber pass applied to
        `autonumber_target` (default "document", i.e. the WHOLE document
        in reading order, same default chemdraw_autonumber itself uses --
        """ + TARGET_DOC + """ Pass the new entries' own object_ids (from
        this call's own `entries[*].object_id`, JSON-encoded) instead if
        there is other content on the page you do NOT want renumbered.
        scheme="numeric-letter" needs autonumber_group_sizes (see
        chemdraw_autonumber).

        KNOWN CAVEAT (confirmed by reading _layout.py): chemdraw_
        build_scope_table's own per-entry label caption and chemdraw_
        autonumber's numbering caption both anchor to the SAME point
        below each structure (its bottom edge + 12pt) -- since autonumber
        runs after the entries are placed, the two captions can land on
        top of each other for every entry that got a non-empty label.
        ALWAYS check `violations.caption_overlap` (a list of
        [caption_id, caption_id] pairs among this call's own entries,
        computed from chemdraw_get_layout's real post-autonumber caption
        bounds -- not a visual guess); if it's non-empty, follow up with
        chemdraw_fix_caption_gaps (or chemdraw_move_objects) to separate
        them, or re-run with labels=[""]*len(substituents) so autonumber's
        stamp is the only caption drawn (the per-entry chemistry data is
        still returned in this call's `entries`, drawn or not).

        check_warnings_target (default "document", run automatically at
        the end) is passed to chemdraw_check_warnings -- ALWAYS check the
        returned `warnings.flagged` list (valence errors etc.) before
        treating the figure as done.

        Also ALWAYS check `violations.off_page` (from
        chemdraw_build_scope_table -- enough entries can run the grid past
        the bottom of the document's real page even though the preview
        image, auto-cropped to whatever was drawn, looks fine) and
        `failed_substituents`. A document backup is saved before insertion
        (backup_path) -- note chemdraw_autonumber does not itself snapshot,
        so restoring that backup undoes both the new grid AND its
        numbering together.

        Returns one merged JSON object: `scaffold`, `entries` (one dict per
        drawn derivative -- substituent, smiles, every requested property,
        label, object_id, fragment_ids, substituent_index), `failed_
        substituents`, `columns`, `page_width_points`, `violations`,
        `backup_path`, `numbered` (chemdraw_autonumber's own result),
        `warnings` (chemdraw_check_warnings' own result), plus the
        rendered preview image."""
        if labels is not None and len(labels) != len(substituents):
            raise ValueError(
                f"labels has {len(labels)} entries but substituents has "
                f"{len(substituents)} -- they must line up positionally, "
                "one label per substituent, in the same order (a "
                "substituent that later fails enumeration just has its "
                "label go unused, it does not shift the rest)."
            )

        enum_result = bridge.enumerate_derivatives(
            substituents, scaffold or None, format,
            properties or ["mw", "formula"])
        rows = enum_result["derivatives"]

        if not rows:
            return as_json({
                "scaffold": enum_result["scaffold"],
                "entries": [],
                "failed_substituents": enum_result["failed"],
                "warning": (
                    "Every substituent failed enumeration -- nothing was "
                    "inserted, arranged, numbered, or checked. See "
                    "failed_substituents for why each one failed."
                ),
            })

        original_indices = _map_to_original_indices(substituents, rows)

        table_entries = []
        labels_used = []
        for k, (row, orig_idx) in enumerate(zip(rows, original_indices)):
            if labels is not None and orig_idx is not None:
                label = labels[orig_idx]
            else:
                label = _auto_label(k + 1, row)
            labels_used.append(label)
            table_entries.append({
                "representation": row["smiles"],
                "format": "smiles",
                "label": label,
            })

        table_result = bridge.build_scope_table(
            table_entries, columns or None, layout, page_width_in or None)

        autonumber_result = bridge.autonumber(
            _parse(autonumber_target), autonumber_start, autonumber_scheme,
            autonumber_bold, autonumber_group_sizes)

        warnings_result = bridge.check_warnings(_parse(check_warnings_target))

        # Catch the KNOWN CAVEAT above (label caption + autonumber caption
        # sharing one anchor point) with real post-placement geometry
        # instead of asking the caller to eyeball the preview image --
        # same "measure the real result" approach _reaction.py's own
        # violations.overlapping already uses. Scoped to just THIS call's
        # own entries (matched via each caption's tag_owner_id, which
        # _add_caption_to_unit stamps with the owning structure's
        # object_id for both the label and the numbering caption) so an
        # unrelated caption already on the page can't produce a false
        # positive.
        entry_ids = set(table_result["object_ids"])
        own_captions = [
            c for c in bridge.get_layout()["captions"]
            if c.get("tag_owner_id") in entry_ids
        ]
        caption_boxes = [
            layout_math.Box(c["bounds"]["left"], c["bounds"]["top"],
                            c["bounds"]["right"], c["bounds"]["bottom"])
            for c in own_captions
        ]
        caption_overlap = [
            list(p) for p in layout_math.find_overlaps(
                caption_boxes, ids=[c["id"] for c in own_captions])
        ]
        violations = dict(table_result["violations"])
        violations["caption_overlap"] = caption_overlap

        entries_out = []
        for k, (row, orig_idx, label) in enumerate(
                zip(rows, original_indices, labels_used)):
            entry = dict(row)  # substituent, smiles, + requested properties
            entry["label"] = label
            entry["object_id"] = table_result["object_ids"][k]
            entry["fragment_ids"] = table_result["fragment_ids"][k]
            entry["substituent_index"] = orig_idx
            entries_out.append(entry)

        merged = {
            "scaffold": enum_result["scaffold"],
            "entries": entries_out,
            "failed_substituents": enum_result["failed"],
            "columns": table_result["columns"],
            "page_width_points": table_result["page_width_points"],
            "violations": violations,
            "backup_path": table_result["backup_path"],
            "numbered": autonumber_result["numbered"],
            "warnings": warnings_result,
            "preview_png_base64": table_result.get("preview_png_base64"),
        }
        return with_preview(merged)
