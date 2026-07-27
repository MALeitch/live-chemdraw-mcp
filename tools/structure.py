"""Document/session, selection, structure I/O, and manipulation tools."""
import base64
import json

from mcp.server.fastmcp import Image

from ._common import TARGET_DOC, as_json


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_status() -> str:
        """Check the connection to ChemDraw: which instance/version is attached,
        the active document, and how many real structures (molecules) are on
        the page, plus captions_on_page, boxes_on_page, and a
        structures_per_box breakdown (structure count per panel box, with a
        box_index of null for anything outside every box) — enough to see
        the shape of a scope page without a full describe_canvas/
        get_document_state call. Caption-wrapper duplicate groups (a
        structure grouped with its own label caption enumerates as two
        groups internally) and empty decoration groups (box frames,
        standalone captions) are excluded from the count and reported
        separately under excluded_units. For full per-structure detail on a
        large page, use region-scoped chemdraw_describe_canvas or
        chemdraw_export_canvas_table rather than dumping the whole page.
        Call this first if anything seems off. Resolves the active
        document even when app.ActiveDocument returns the known-flaky
        None with 2+ documents open (the common case here, since this
        connector's own scratch document is deliberately kept open
        alongside your real one) — falls back to the last document this
        connector actively worked on rather than silently omitting
        structures_on_page/captions_on_page/boxes_on_page."""
        return as_json(bridge.status())

    @mcp.tool()
    def chemdraw_new_document() -> str:
        """Create a new empty ChemDraw document and make it active.
        IChemDrawDocument.Close() itself is a confirmed no-op over COM, but
        chemdraw_close_document works around that via a Win32 window-close,
        so a document created here isn't permanently stuck -- call it when
        you're done with this one. For temporary/test work, prefer
        chemdraw_use_scratch_document instead of creating a new document at
        all."""
        return as_json(bridge.new_document())

    @mcp.tool(description=(
        "Close one open document by name (chemdraw_list_documents for the "
        "list). Works around IChemDrawDocument.Close() being a confirmed "
        "no-op over COM by posting a Win32 close directly to that "
        "document's own window instead. A document with unsaved changes is "
        "refused unless discard_changes=true -- save it first "
        "(chemdraw_save_document) if you want to keep the changes, since "
        "there is no way to answer ChemDraw's own \"Save changes?\" prompt "
        "for it once this starts. If the closed document was the active "
        "one, `new_active_document` reports whichever document ChemDraw "
        "switched to automatically (MDI default), or null if none remain."))
    def chemdraw_close_document(name: str, discard_changes: bool = False) -> str:
        return as_json(bridge.close_document(name, discard_changes))

    @mcp.tool()
    def chemdraw_use_scratch_document() -> str:
        """Activate the single reusable scratch document
        (chemdraw-mcp-scratch.cdxml), CLEARING whatever it contained from
        last time. Prefer this over chemdraw_new_document for throwaway
        work — trial structures, experiments, verification — it's one
        window reused in place rather than a new one each time; even
        though chemdraw_close_document can clean up a new document
        afterward, reusing the scratch document avoids the extra round
        trip entirely."""
        return as_json(bridge.use_scratch_document())

    @mcp.tool()
    def chemdraw_open_document(path: str) -> str:
        """Open an existing ChemDraw file (.cdx/.cdxml/.mol/...) as the
        active document. If the open lands but ChemDraw's own COM return
        value comes back empty, this re-resolves the real document by
        scanning open documents rather than failing -- if you still get an
        error here right after writing the file yourself, retry once
        before assuming the file is bad; a persistent failure means
        ChemDraw genuinely rejected it."""
        return as_json(bridge.open_document(path))

    @mcp.tool()
    def chemdraw_save_document(path: str = "", overwrite: bool = False) -> str:
        """Save the active document. With path, does Save As to that path.

        Save As onto a DIFFERENT file that already exists on disk requires
        overwrite=true — otherwise it's refused to avoid silently clobbering
        something unrelated. Re-saving the document you already have open
        (path omitted, or path == its current file) never needs this."""
        return as_json(bridge.save_document(path or None, overwrite))

    @mcp.tool(description=(
        "Convert a ChemDraw file between .cdx and .cdxml (or any other "
        "ChemDraw-readable/writable format) by extension — ChemDraw's own "
        "SaveAs infers the format from output_path's extension, same as "
        "chemdraw_save_document. Opens input_path in the background, saves "
        "it as output_path, then closes that background document and "
        "restores whichever document was active before this call — it "
        "will not disturb a document you already had open, unless "
        "input_path was ITSELF that already-open document (then its "
        "window now points at output_path, exactly like calling "
        "chemdraw_save_document on it directly — nothing to restore). "
        "Refuses to overwrite an existing output_path unless overwrite=true."))
    def chemdraw_convert_cdx_cdxml(input_path: str, output_path: str,
                                   overwrite: bool = False) -> str:
        return as_json(bridge.convert_cdx_cdxml(input_path, output_path, overwrite))

    @mcp.tool()
    def chemdraw_undo() -> str:
        """Undo the last operation the USER performed by hand in ChemDraw.
        WARNING (probed live): edits made through this connector do NOT land
        on ChemDraw's undo stack — this call returns ok but reverts nothing.
        To roll back a connector operation, reopen the backup_path the
        mutating tool returned, or use chemdraw_expand_shorthand to reverse
        a contraction."""
        return as_json(bridge.undo())

    @mcp.tool()
    def chemdraw_redo() -> str:
        """Redo the last undone ChemDraw operation."""
        return as_json(bridge.redo())

    @mcp.tool()
    def chemdraw_list_documents() -> str:
        """List all open ChemDraw documents and which one is active."""
        return as_json(bridge.list_documents())

    @mcp.tool()
    def chemdraw_set_active_document(name: str) -> str:
        """Switch which open ChemDraw document subsequent tools act on."""
        return as_json(bridge.set_active_document(name))

    @mcp.tool()
    def chemdraw_get_selection() -> str:
        """Describe what the user currently has selected in the ChemDraw window
        (id, formula, atom/bond counts, position). Use this to act on whatever
        the user is pointing at."""
        return as_json(bridge.get_selection())

    @mcp.tool()
    def chemdraw_select(object_id: str) -> str:
        """Select a structure in the live ChemDraw window by object_id, giving
        the user a visible highlight of what you're about to act on."""
        return as_json(bridge.select(object_id))

    @mcp.tool()
    def chemdraw_insert_structure(
        representation: str,
        format: str = "smiles",
        x: float | None = None,
        y: float | None = None,
    ) -> str:
        """Draw a structure into the active ChemDraw document.

        format: smiles | molfile | inchi | name (chemical name, e.g. 'aspirin')
        | cdxml | cml | helm. Optional x/y places the CENTER OF THE WHOLE
        INSERTION at that point (in points, 72/inch, top-left origin),
        including literal (0, 0); omit BOTH to let ChemDraw choose. A
        representation with disconnected fragments (a salt like
        "[K+].[O-]C(=O)[O-].[K+]", or a name-resolved organometallic like
        "PdCl2(PPh3)2") inserts as more than one real ChemDraw object —
        `inserted` has one entry per fragment, each with its own
        object_id — but x/y moves them together as one rigid group
        (ChemDraw's own relative placement between fragments is preserved,
        not collapsed onto a single point). Returns `inserted` (one entry
        per fragment) plus `off_page`: non-empty if an explicit x/y (or
        ChemDraw's own auto-placement) put any fragment outside the
        document's actual page — there is no preview image on this call
        to catch it visually, so check this field directly.

        Also returns `wrapper_groups`: for a disconnected-fragment payload,
        ChemDraw additionally creates an invisible wrapper Group around
        those fragments (bounds/formula = the UNION of its children, e.g.
        "HNa" wrapping a "Na" and an "H" ion) that is NOT one of the
        `inserted` fragments and must never be moved/positioned on its own
        (it double-moves the same underlying atoms as its children) — but
        it IS a real, addressable object, and its own id is the right one
        to use if you want to reference the whole combined reagent as a
        single unit (e.g. one row in chemdraw_make_stoichiometry_table,
        instead of two separate ion rows each with an individually wrong
        molecular weight). Each entry has the wrapper's own id/formula/
        bounds plus `wraps` (the ids of the fragments it contains). Empty
        for an ordinary single-fragment insertion.

        A query immediately after this call (chemdraw_get_document_state,
        chemdraw_status, chemdraw_describe_canvas) is safe to trust on the
        first try — this call invalidates the shared per-document cache
        and settles ChemDraw's window state before returning, so the
        newly-inserted structure is never missing from the very next
        query (a real, confirmed-live gap before 2026-07-24)."""
        if (x is None) != (y is None):
            raise ValueError("x and y must be given together, or both omitted")
        pos = (x, y) if x is not None else None
        return as_json(bridge.insert_structure(representation, format, pos))

    @mcp.tool(description=(
        "Insert every structure from a .mol or .sdf file. A .sdf with "
        "multiple $$$$-terminated records inserts each one (data fields "
        "after each record's connection table are ignored — only the "
        "molfile block itself is used); a .mol is a single record. Each "
        "record is inserted independently — one malformed record is "
        "reported in `failed` without aborting the rest, same isolation as "
        "chemdraw_edit_atoms/chemdraw_edit_bonds. target='scratch' "
        "(default) clears and reuses the shared scratch document first "
        "(see chemdraw_use_scratch_document); target='current' inserts "
        "into whatever document is already active. ChemDraw does not "
        "auto-arrange multiple records — follow up with "
        "chemdraw_arrange_grid if you imported more than one and want "
        "them laid out."))
    def chemdraw_import_molfile(path: str, target: str = "scratch") -> str:
        return as_json(bridge.import_molfile(path, target))

    @mcp.tool(description=(
        "Export structures as text. format: smiles | molfile | inchi | "
        "inchikey | cdxml | cml | helm. " + TARGET_DOC))
    def chemdraw_export_structure(format: str = "molfile",
                                  target: str = "selection") -> str:
        return as_json(bridge.export_structure(format, _parse(target)))

    @mcp.tool(description=(
        "Export CDXML text to a file. Unlike chemdraw_save_document(path="
        "'*.cdxml'), this does NOT change the active document's associated "
        "file / mark it saved-to-that-path — it's a side-channel snapshot, "
        "and target can be 'selection' (only the current selection) as "
        "well as 'document' (the whole page). Refuses to overwrite an "
        "existing file unless overwrite=true."))
    def chemdraw_export_cdxml(path: str, target: str = "document",
                              overwrite: bool = False) -> str:
        return as_json(bridge.export_cdxml(path, target, overwrite))

    @mcp.tool(description=(
        "Render structures to an image. format: png | svg | emf. With path, "
        "writes the file and returns the path (refuses to overwrite an "
        "existing file unless overwrite=true); without, returns the image "
        "inline so you can look at it. " + TARGET_DOC))
    def chemdraw_export_image(format: str = "png", target: str = "document",
                              path: str = "", dpi: int = 300,
                              overwrite: bool = False):
        result = bridge.export_image(format, _parse(target), path or None, dpi,
                                     overwrite)
        if "base64" in result and format == "png":
            return [f"Rendered {format} at {dpi} dpi.",
                    Image(data=base64.b64decode(result["base64"]), format="png")]
        return as_json(result)

    @mcp.tool(description=(
        "Copy structures to the system clipboard exactly like ChemDraw's own "
        "Ctrl+C — pasteable into Word/PowerPoint as an editable object or "
        "image. " + TARGET_DOC))
    def chemdraw_copy_to_clipboard(target: str = "selection") -> str:
        return as_json(bridge.copy_to_clipboard(_parse(target)))

    @mcp.tool(description=(
        "Transform structures, or an arbitrary SUB-SELECTION of one "
        "structure's atoms/bonds (pass atom_refs/bond_refs from "
        "chemdraw_list_atoms or chemdraw_split_at_bond — e.g. to fold/flip "
        "one branch of a molecule without rescaling the whole thing, which "
        "would break the shared bond-length convention across a figure). "
        "action: move (dx/dy points) | rotate (degrees) | scale (factor) | "
        "flip (vertical=true for vertical) | clean (tidy the drawing, "
        "ChemDraw's Clean Up Structure). When refs are given, target must "
        "resolve to exactly one structure. WARNING: flipping/rotating only "
        "part of a structure can distort bond length/angle at the "
        "connection point to the untouched part — follow up with "
        "chemdraw_transform(..., action='clean') on the whole structure "
        "afterward to fix that. target may also be (or, as a list, "
        "include) a free-floating annotation id — an arrow, symbol, "
        "bracket, tlc_plate, or caption including an unowned one (e.g. a "
        "reaction scheme's reagents_text/conditions_text) — instead of a "
        "structure's; only action='move' is supported for those (they have "
        "no chemical structure for rotate/scale/flip/clean to act on), and "
        "an unsupported action against one is reported per-item in "
        "`failed` rather than raised. atom_refs/bond_refs cannot target an "
        "annotation (it has no atoms/bonds); doing so raises a clear error "
        "naming what target actually resolved to. " + TARGET_DOC))
    def chemdraw_transform(target: str = "selection", action: str = "clean",
                           dx: float = 0, dy: float = 0, degrees: float = 0,
                           factor: float = 1.0, vertical: bool = False,
                           atom_refs: list[str] | None = None,
                           bond_refs: list[str] | None = None) -> str:
        return as_json(bridge.transform(_parse(target), action, dx=dx, dy=dy,
                                        degrees=degrees, factor=factor,
                                        vertical=vertical, atom_refs=atom_refs,
                                        bond_refs=bond_refs))

    @mcp.tool(description=(
        "Delete structures from the document. " + TARGET_DOC +
        " (no default — deleting requires an explicit target). "
        "target=\"document\"/\"selection\" only ever deletes real "
        "structures, but a single object_id or a JSON list of them may "
        "ALSO name a caption (id from chemdraw_get_layout/"
        "chemdraw_describe_canvas's captions list), or an arrow/symbol/"
        "bracket/tlc_plate (id from chemdraw_list_arrows/chemdraw_list_"
        "symbols/chemdraw_list_brackets/chemdraw_list_tlc_plates) — this "
        "is the way to clean up an orphaned caption (its owning structure "
        "already deleted) or a bad TLC plate/arrow/bracket, none of which "
        "chemdraw_undo can reach (it only reverts the user's own manual "
        "edits, never this connector's own mutations)."))
    def chemdraw_remove(target: str) -> str:
        return as_json(bridge.remove(_parse(target)))

    @mcp.tool(description=(
        "List a structure's atoms and bonds in one call: element, charge, "
        "position, and a stable ref (e.g. 'a42') for each atom; order, "
        "endpoints, and a stable ref (e.g. 'b12-45') for each bond. Call "
        "this once before editing atoms/bonds you didn't just create, then "
        "pass the ref to chemdraw_edit_atom/chemdraw_edit_bond/"
        "chemdraw_add_atom/chemdraw_set_bond_stereo instead of guessing a "
        "1-based index. " + TARGET_DOC))
    def chemdraw_list_atoms(target: str = "selection") -> str:
        return as_json(bridge.list_atoms_bonds(_parse(target)))

    @mcp.tool(description=(
        "Compute which atoms/bonds lie on one side of a bond, WITHOUT "
        "changing the document — the offline-planning step for folding or "
        "flipping one branch of a molecule to fit a bounding box (rescaling "
        "the whole structure instead is bad practice: every structure in a "
        "figure should share the same scale/bond length). Pick the bond to "
        "split at (bond_ref from chemdraw_list_atoms) and one atom_ref "
        "anywhere on the side you want to affect, then pass the returned "
        "atom_refs/bond_refs straight into chemdraw_transform — the same "
        "'compute the whole plan, then execute once' shape as "
        "chemdraw_get_layout -> chemdraw_move_objects. Raises an error if "
        "the bond is part of a ring — there is no clean 'one side' of a "
        "ring bond. " + TARGET_DOC))
    def chemdraw_split_at_bond(target: str, bond_ref: str, side_atom_ref: str) -> str:
        return as_json(bridge.split_at_bond(_parse(target), bond_ref, side_atom_ref))

    @mcp.tool(description=(
        "Change an existing atom's element (symbol, e.g. 'N'), formal "
        "charge (pass set_charge=true to apply charge, so 0 can be set "
        "explicitly), and/or isotope label (pass set_isotope=true; e.g. "
        "isotope=13 for 13C, isotope=2 for D — confirmed to survive export "
        "as real isotope notation: SMILES '[13CH2]', molfile 'M  ISO' "
        "block, not just a ChemDraw-display-only label). KNOWN LIMITATION, "
        "confirmed live: setting isotope=0 to clear a label back to "
        "natural abundance can silently fail to take effect if the atom's "
        "isotope is already nonzero — this is a general ChemDraw quirk "
        "(charge=0 has the identical failure mode from a nonzero charge), "
        "not specific to this tool; always check the returned `isotope` "
        "field to see whether a reset actually landed. atom_index accepts "
        "either a 1-based position within the structure (shifts as atoms "
        "are added/removed) or a stable ref from chemdraw_list_atoms (e.g. "
        "'a42', recommended when editing more than one atom in a structure "
        "you didn't just create). " + TARGET_DOC))
    def chemdraw_edit_atom(target: str, atom_index: int | str, element: str = "",
                           charge: int = 0, set_charge: bool = False,
                           isotope: int = 0, set_isotope: bool = False) -> str:
        return as_json(bridge.edit_atom(
            _parse(target), atom_index, element or None,
            charge if set_charge else None,
            isotope if set_isotope else None))

    @mcp.tool(description=(
        "Change an existing bond's order: single | double | triple | "
        "aromatic | dative. bond_index accepts either a 1-based position "
        "within the structure or a stable ref from chemdraw_list_atoms "
        "(e.g. 'b12-45'). " + TARGET_DOC))
    def chemdraw_edit_bond(target: str, bond_index: int | str,
                           bond_order: str = "") -> str:
        return as_json(bridge.edit_bond(_parse(target), bond_index,
                                        bond_order or None))

    @mcp.tool(description=(
        "Apply many atom edits in ONE call instead of one call per atom — "
        "the fast path once chemdraw_list_atoms has given you the refs for "
        "everything you need to change. edits: list like "
        "[{\"target\": \"claude-...\", \"atom\": \"a42\", \"element\": "
        "\"N\"}, {\"target\": \"claude-...\", \"atom\": \"a57\", "
        "\"set_charge\": true, \"charge\": -1}, {\"target\": \"claude-...\", "
        "\"atom\": \"a12\", \"set_isotope\": true, \"isotope\": 13}, ...] "
        "(atom accepts a ref or a 1-based index). Returns `applied` and "
        "`failed` per-item, "
        "plus `unexpected_changes` — any structure that changed WITHOUT "
        "being in your list — the same before/after diff "
        "chemdraw_move_objects uses."))
    def chemdraw_edit_atoms(edits: list[dict]) -> str:
        return as_json(bridge.edit_atoms(edits))

    @mcp.tool(description=(
        "Apply many bond edits in ONE call instead of one call per bond — "
        "the fast path once chemdraw_list_atoms has given you the refs for "
        "everything you need to change. edits: list like "
        "[{\"target\": \"claude-...\", \"bond\": \"b12-45\", "
        "\"bond_order\": \"double\"}, ...] (bond accepts a ref or a "
        "1-based index). Returns `applied` and `failed` per-item, plus "
        "`unexpected_changes` — any structure that changed WITHOUT being "
        "in your list — the same before/after diff chemdraw_move_objects "
        "uses."))
    def chemdraw_edit_bonds(edits: list[dict]) -> str:
        return as_json(bridge.edit_bonds(edits))

    @mcp.tool(description=(
        "Grow a structure by bonding a new atom to an existing one, then "
        "auto-cleaning the geometry. attach_to_atom_index accepts either a "
        "1-based position or a stable ref from chemdraw_list_atoms (e.g. "
        "'a42'). " + TARGET_DOC))
    def chemdraw_add_atom(target: str, attach_to_atom_index: int | str, element: str,
                          bond_order: str = "single") -> str:
        return as_json(bridge.add_atom(_parse(target), attach_to_atom_index,
                                       element, bond_order))


def _parse(target):
    """Resolve a target string into what targets.resolve() expects:
    "selection"/"document" literally, a single object_id, or a list of
    object_ids.

    Accepts a real JSON array (`["id1","id2"]`, what TARGET_DOC tells
    callers to send) via json.loads -- a bare comma-split here would
    instead shred the brackets/quotes into the list items themselves
    (confirmed live: `["id1","id2"]` split naively on "," produced
    `['["id1"', '"id2"]']`, so every batch call using JSON-array syntax
    silently failed with garbled ids). A plain comma-separated string with
    no brackets (`"id1,id2"`) is still accepted as a convenience fallback
    for anyone hand-typing a target rather than JSON-encoding it."""
    target = target.strip()
    if target in ("selection", "document"):
        return target
    if target.startswith("["):
        try:
            parsed = json.loads(target)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, list):
                return [str(p).strip() for p in parsed if str(p).strip()]
    if "," in target:
        return [p.strip() for p in target.split(",") if p.strip()]
    return target
