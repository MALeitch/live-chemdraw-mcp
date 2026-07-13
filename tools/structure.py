"""Document/session, selection, structure I/O, and manipulation tools."""
import base64

from mcp.server.fastmcp import Image

from ._common import TARGET_DOC, as_json


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_status() -> str:
        """Check the connection to ChemDraw: which instance/version is attached,
        the active document, and how many structures are on the page. Call this
        first if anything seems off."""
        return as_json(bridge.status())

    @mcp.tool()
    def chemdraw_new_document() -> str:
        """Create a new empty ChemDraw document and make it active.
        NOTE: documents can NOT be closed over COM (Close() is a no-op), so
        every new document is a permanent window until the user closes it by
        hand. For temporary/test work use chemdraw_use_scratch_document
        instead."""
        return as_json(bridge.new_document())

    @mcp.tool()
    def chemdraw_use_scratch_document() -> str:
        """Activate the single reusable scratch document
        (chemdraw-mcp-scratch.cdxml), CLEARING whatever it contained from
        last time. Use this instead of chemdraw_new_document for throwaway
        work — trial structures, experiments, verification — because COM
        cannot close documents and each new one permanently clutters the
        user's ChemDraw window."""
        return as_json(bridge.use_scratch_document())

    @mcp.tool()
    def chemdraw_open_document(path: str) -> str:
        """Open an existing ChemDraw file (.cdx/.cdxml/.mol/...) as the active document."""
        return as_json(bridge.open_document(path))

    @mcp.tool()
    def chemdraw_save_document(path: str = "") -> str:
        """Save the active document. With path, does Save As to that path."""
        return as_json(bridge.save_document(path or None))

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
        x: float = 0,
        y: float = 0,
    ) -> str:
        """Draw a structure into the active ChemDraw document.

        format: smiles | molfile | inchi | name (chemical name, e.g. 'aspirin')
        | cdxml | cml | helm. Optional x/y places the structure's center at
        that point (in points, 72/inch, top-left origin); omit to let ChemDraw
        choose. Returns the new structure's object_id for later reference."""
        pos = (x, y) if (x or y) else None
        return as_json(bridge.insert_structure(representation, format, pos))

    @mcp.tool(description=(
        "Export structures as text. format: smiles | molfile | inchi | "
        "inchikey | cdxml | cml | helm. " + TARGET_DOC))
    def chemdraw_export_structure(format: str = "molfile",
                                  target: str = "selection") -> str:
        return as_json(bridge.export_structure(format, _parse(target)))

    @mcp.tool(description=(
        "Render structures to an image. format: png | svg | emf. With path, "
        "writes the file and returns the path; without, returns the image "
        "inline so you can look at it. " + TARGET_DOC))
    def chemdraw_export_image(format: str = "png", target: str = "document",
                              path: str = "", dpi: int = 300):
        result = bridge.export_image(format, _parse(target), path or None, dpi)
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

    @mcp.tool()
    def chemdraw_list_objects() -> str:
        """List every structure on the page with id, formula, counts, and
        position — including ones the user drew by hand."""
        return as_json(bridge.list_objects())

    @mcp.tool(description=(
        "Transform structures. action: move (dx/dy points) | rotate (degrees) "
        "| scale (factor) | flip (vertical=true for vertical) | clean (tidy "
        "the drawing, ChemDraw's Clean Up Structure). " + TARGET_DOC))
    def chemdraw_transform(target: str = "selection", action: str = "clean",
                           dx: float = 0, dy: float = 0, degrees: float = 0,
                           factor: float = 1.0, vertical: bool = False) -> str:
        return as_json(bridge.transform(_parse(target), action, dx=dx, dy=dy,
                                        degrees=degrees, factor=factor,
                                        vertical=vertical))

    @mcp.tool(description=(
        "Delete structures from the document. " + TARGET_DOC +
        " (no default — deleting requires an explicit target)."))
    def chemdraw_remove(target: str) -> str:
        return as_json(bridge.remove(_parse(target)))

    @mcp.tool(description=(
        "Change an existing atom's element (symbol, e.g. 'N') and/or formal "
        "charge (pass set_charge=true to apply charge, so 0 can be set "
        "explicitly). atom_index is 1-based within the structure. "
        + TARGET_DOC))
    def chemdraw_edit_atom(target: str, atom_index: int, element: str = "",
                           charge: int = 0, set_charge: bool = False) -> str:
        return as_json(bridge.edit_atom(
            _parse(target), atom_index, element or None,
            charge if set_charge else None))

    @mcp.tool(description=(
        "Change an existing bond's order: single | double | triple | "
        "aromatic | dative. bond_index is 1-based within the structure. "
        + TARGET_DOC))
    def chemdraw_edit_bond(target: str, bond_index: int,
                           bond_order: str = "") -> str:
        return as_json(bridge.edit_bond(_parse(target), bond_index,
                                        bond_order or None))

    @mcp.tool(description=(
        "Grow a structure by bonding a new atom to an existing one, then "
        "auto-cleaning the geometry. " + TARGET_DOC))
    def chemdraw_add_atom(target: str, attach_to_atom_index: int, element: str,
                          bond_order: str = "single") -> str:
        return as_json(bridge.add_atom(_parse(target), attach_to_atom_index,
                                       element, bond_order))


def _parse(target):
    """Allow a JSON-style comma list in the target string."""
    target = target.strip()
    if "," in target and target not in ("selection", "document"):
        return [p.strip() for p in target.split(",") if p.strip()]
    return target
