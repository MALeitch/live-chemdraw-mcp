"""CDXML highlight-color injection -- reproduces ChemDraw's own "Highlight
Color" GUI tool (select a region, hit Apply), which has NO matching
COM-settable property. Confirmed live (2026-07-31): exhaustively absent
from every property IChemDrawObject/Atom/Bond/Text/Graphic/Group expose
(full typelib deep-dump), and 9 direct name-guesses (HighlightColor,
HilightColor, BackgroundColor, ...) tried directly on a genuinely
GUI-highlighted atom all failed. Highlighted (the one COM property that
DOES exist and IS settable) is a real, different thing -- confirmed by
the user directly, live, after comparing output: it recolors the drawn
line/label text itself (opaque, replacing black), not the translucent
wash behind the original black structure the real tool produces.

The real mechanism is a file-format attribute: `highlightColor="N"` on
<n>/<b> CDXML elements, N referencing a <colortable> entry with a
CONFIRMED +2 index offset (list position 0 -> attribute index 2, ...
positions/indices 0-1 are reserved, never part of the visible
<colortable> list -- verified by diffing a real document's CDXML
before/after the user applied a real highlight via the GUI: exactly one
new <color> entry appeared, appended at the end of the list, referenced
by attribute value == (list length before the append) + 2). Reproduced
end-to-end and confirmed by the user against their own real example:
export a structure's own CDXML, inject this attribute here, reimport via
the same Data-property mechanism structure insertion already uses --
renders a real translucent color wash behind the original black
structure, visually identical to the GUI tool.

Zero COM imports; pure XML, mirrors domain/cdxml_graph.py's approach.
"""
import xml.etree.ElementTree as ET

_PROLOG = ('<?xml version="1.0" encoding="UTF-8" ?>'
           '<!DOCTYPE CDXML SYSTEM '
           '"https://static.chemistry.revvitycloud.com/cdxml/CDXML.dtd">')

# The 8 colors ChemDraw always seeds a fresh colortable with, confirmed
# live from real document exports -- used only when a per-unit CDXML
# fragment export has no <colortable> of its own to build one from.
_STANDARD_COLORS = [
    (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0), (0.0, 1.0, 1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 1.0),
]

_INDEX_OFFSET = 2  # confirmed live -- see module docstring
_COLOR_TOL = 0.002  # float round-trip slop for "is this the same color"


def parse_rgb_hex(hex_str):
    """'#RRGGBB' -> (r, g, b) floats in 0..1, the format CDXML <color>
    elements use (distinct from com/types.py's rgb_hex_to_colorref,
    which targets the COM Color property's packed-int BGR format --
    this module is intentionally COM-free, so it doesn't share that
    helper)."""
    s = hex_str.lstrip("#")
    if len(s) != 6:
        raise ValueError(
            f"Expected a 6-digit hex color like '#FF8000', got {hex_str!r}")
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        raise ValueError(
            f"Expected a 6-digit hex color like '#FF8000', got {hex_str!r}"
        ) from None
    return r / 255.0, g / 255.0, b / 255.0


def ensure_full_document(cdxml_text):
    """A per-unit CDXML export (targets.unit_objects(u).GetData('text/xml'))
    is a bare <fragment>...</fragment>, not a complete <CDXML> document --
    confirmed by inspecting real export output (unlike whole-document/
    selection export, which chemdraw_export_cdxml's own docstring already
    notes comes back complete with header/colortable/fonttable). Wrap a
    bare fragment in a minimal valid shell so it parses and re-imports the
    same way domain/reagent_text.py's from-scratch CDXML does. A payload
    that's already a full document (starts with '<?xml' or '<CDXML') is
    returned unchanged."""
    stripped = cdxml_text.lstrip()
    if stripped.startswith("<?xml") or stripped.startswith("<CDXML"):
        return cdxml_text
    return f'{_PROLOG}<CDXML><page>{cdxml_text}</page></CDXML>'


def parse(cdxml_text):
    """CDXML text -> ElementTree root, after ensure_full_document."""
    return ET.fromstring(ensure_full_document(cdxml_text))


def resolve_color_index(root, hex_color):
    """Find-or-append hex_color in root's <colortable> (creating one,
    seeded with _STANDARD_COLORS, if this document has none at all --
    the per-unit-fragment case). Returns the color's attribute index
    (list position + _INDEX_OFFSET), reusing an existing near-match
    entry instead of appending a duplicate when one is already present
    within _COLOR_TOL."""
    r, g, b = parse_rgb_hex(hex_color)
    colortable = root.find("colortable")
    if colortable is None:
        colortable = ET.Element("colortable")
        for cr, cg, cb in _STANDARD_COLORS:
            ET.SubElement(colortable, "color",
                          {"r": repr(cr), "g": repr(cg), "b": repr(cb)})
        root.insert(0, colortable)
    entries = colortable.findall("color")
    for i, c in enumerate(entries):
        cr = float(c.get("r", "0"))
        cg = float(c.get("g", "0"))
        cb = float(c.get("b", "0"))
        if (abs(cr - r) < _COLOR_TOL and abs(cg - g) < _COLOR_TOL
                and abs(cb - b) < _COLOR_TOL):
            return i + _INDEX_OFFSET
    ET.SubElement(colortable, "color", {"r": repr(r), "g": repr(g), "b": repr(b)})
    return len(entries) + _INDEX_OFFSET


def _bond_pair_set(bond_pairs):
    return {frozenset(p) for p in bond_pairs} if bond_pairs else set()


def set_highlight(root, index, atom_ids=None, bond_pairs=None):
    """Set highlightColor=index on every <n> whose id is in atom_ids (ALL
    <n> elements if atom_ids is None -- the "select the whole structure"
    case) and every <b> whose (B, E) atom-id pair is in bond_pairs
    (matching either order -- a bond's own B/E order isn't guaranteed to
    match the caller's pair order; ALL <b> elements if bond_pairs is
    None). Returns (atom_count, bond_count) actually matched/modified --
    the caller needs this because after a delete+reimport round trip the
    reimported atoms/bonds get FRESH numeric ids (confirmed live: NOT the
    same ones atom_ids/bond_pairs were expressed in), so there is no way
    to re-derive "how many were highlighted" from the post-reimport
    structure by id comparison; counting here, before that renumbering
    happens, is the only reliable source for it."""
    want_all_atoms = atom_ids is None
    want_all_bonds = bond_pairs is None
    atom_id_set = set(atom_ids) if atom_ids else set()
    pair_set = _bond_pair_set(bond_pairs)
    atom_count = bond_count = 0
    for n in root.iter("n"):
        nid = n.get("id")
        if nid is not None and (want_all_atoms or int(nid) in atom_id_set):
            n.set("highlightColor", str(index))
            atom_count += 1
    for b in root.iter("b"):
        bid, eid = b.get("B"), b.get("E")
        if bid is None or eid is None:
            continue
        if want_all_bonds or frozenset({int(bid), int(eid)}) in pair_set:
            b.set("highlightColor", str(index))
            bond_count += 1
    return atom_count, bond_count


def clear_highlight(root, atom_ids=None, bond_pairs=None):
    """Remove highlightColor from matching elements -- same selection
    semantics as set_highlight, ChemDraw's own "un-highlight". Returns
    (atom_count, bond_count) actually matched/modified -- see
    set_highlight's docstring for why the caller can't recompute this
    itself after reimport."""
    want_all_atoms = atom_ids is None
    want_all_bonds = bond_pairs is None
    atom_id_set = set(atom_ids) if atom_ids else set()
    pair_set = _bond_pair_set(bond_pairs)
    atom_count = bond_count = 0
    for n in root.iter("n"):
        nid = n.get("id")
        if nid is not None and (want_all_atoms or int(nid) in atom_id_set):
            n.attrib.pop("highlightColor", None)
            atom_count += 1
    for b in root.iter("b"):
        bid, eid = b.get("B"), b.get("E")
        if bid is None or eid is None:
            continue
        if want_all_bonds or frozenset({int(bid), int(eid)}) in pair_set:
            b.attrib.pop("highlightColor", None)
            bond_count += 1
    return atom_count, bond_count


def serialize(root):
    return _PROLOG + ET.tostring(root, encoding="unicode")
