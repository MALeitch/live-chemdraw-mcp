"""Native ChemDraw Stoichiometry Grid: read/write via the document's own
CDXML text, NOT the live COM object model.

CONFIRMED DEAD END (two separate sessions' live probing, see
docs/com_typelib/ and the "1-save-the-com-fuzzy-hopper" plan file):
IChemDrawStoichiometryGrid's Components/Properties graph is broken for
re-access from any later COM call -- doc.StoichiometryGrids.Item() raises
E_NOTIMPL unconditionally (every calling convention tried: 0/1-based index,
default-member call syntax, the collection's own enumerator -- it doesn't
even support enumeration), a re-fetched grid's .Components always raises
DISP_E_MEMBERNOTFOUND, and IChemDrawSGProperty.Text has no working setter
even via a raw DISPATCH_PROPERTYPUT invoke bypassing pywin32's wrapper
entirely (the server itself returns DISP_E_MEMBERNOTFOUND for that verb on
that DISPID -- a genuine protocol-level rejection, not just an
under-declared type library). Only the ONE COM handle returned directly by
doc.MakeStoichiometryGrid() has a working .Components, for the instant
that call returns -- useless across separate tool calls, which is the only
shape an MCP tool call can take.

CONFIRMED WORKING INSTEAD: the grid's real data is plain XML in the
document's own CDXML export/import format (doc.Objects.GetData("text/xml"),
the same call chemdraw_connector/snapshots.py already uses for backups):

    <stoichiometrygrid id="...">
      <sgcomponent id="..." ComponentReferenceID="<raw ChemDraw Group ID>"
                   ComponentIsReactant="yes">
        <sgdatum id="..." SGDataType="3" SGDataValue="5" SGPropertyType="7">
          <objecttag ...><t ...><s ...>5.00</s><s ...>g</s></t></objecttag>
        </sgdatum>
        ...
      </sgcomponent>
      ...
    </stoichiometrygrid>

SGDataValue is a plain, directly-editable XML attribute holding the real
numeric ground truth. One component per row-label column too (identified
by ComponentIsHeader="yes") -- its sgdatum values are literal row-label
text ("Sample Mass", etc.), not reactant data; always skip it.

Editing SGDataValue (and the matching visible first <s> text run --
leaving the second <s>, the unit suffix, alone) then feeding the modified
CDXML into a FRESH document via Documents.Open() is confirmed live to
work: ChemDraw's own stoichiometry engine recalculates every dependent
field correctly from the edited value on load (verified: editing Sample
Mass 5.00 -> 10.00g on a document with MW=46.07 recalculated Reactant
Moles to 217.07mmol on reopen -- an exact match, not a stale cached
number). Two COM-mutation shortcuts were tried and confirmed NOT to work,
so don't re-attempt them: doc.Objects.SetData (same DISPID as GetData, the
PROPERTYPUT side of a parameterized property -- accepts the call without
raising but is a silent no-op, matching this codebase's other
confirmed-no-op setters; doesn't change the live model at all) and
doc.Selection.SetData (doesn't exist -- AttributeError). Also confirmed:
Documents.Open() on a path that's ALREADY open just reactivates the
existing in-memory window without re-reading disk -- so a write MUST
target a genuinely different file path each time to be picked up; there
is no in-place refresh (see bridge/_stoichiometry.py for how the write
path handles this).

SGPropertyType code -> field mapping below, reverse-engineered from a live
2-reactant test document (ethanol + benzene, arrow-linked selection before
MakeStoichiometryGrid()). Confidence noted per field: HIGH for the ones
that showed a real, visibly-rendered value matching what was actually
typed/computed; MEDIUM for ones whose only evidence is a plausible unit
suffix on a default/zero value that was never actually populated; every
other numeric SGPropertyType is exposed under a synthetic "type_N" field
name rather than guessed at (types 3/11 were never observed in this
reactants-only test -- most likely product-side fields: theoretical
yield/actual yield/percent yield).
"""
import re
import xml.etree.ElementTree as ET

SG_PROPERTY_FIELDS = {
    1: "formula",            # HIGH -- matches the structure's own formula text
    2: "molecular_weight",   # HIGH -- matches "46.07" / "78.11"
    4: "limiting_reagent",   # HIGH -- matches "Yes" / "No"
    5: "limit_moles",        # HIGH -- the header/row-label component's own text for this row reads "Limit Moles"
    6: "equivalents",        # HIGH -- confirmed live: visible+populated ("1.06") for a non-limiting reagent
    7: "sample_mass",        # HIGH -- the field actually live-edited; confirmed to cascade correctly
    8: "percent_weight",     # MEDIUM -- hidden "100.00%" default, inferred from "%" suffix
    9: "volume",             # MEDIUM -- hidden "0.00 mL" default, inferred from "mL" suffix
    10: "molarity",          # MEDIUM -- hidden "0.00 M" default, inferred from "M" suffix
    12: "density",           # MEDIUM -- hidden "0.00 g/mL" default, inferred from "g/mL" suffix
    13: "reactant_moles",    # HIGH -- matches visible "108.53mmol" / "217.07mmol"
    14: "reactant_mass",     # HIGH -- matches visible "5.00g" / "10.00g"
}
_FIELD_TO_TYPE = {v: k for k, v in SG_PROPERTY_FIELDS.items()}

# Fields ChemDraw itself computes from chemistry/other fields -- rejecting
# an edit here up front (in field_to_property_type) is friendlier than
# silently writing SGDataValue on a field ChemDraw will just recompute or
# ignore. Only the ones actually exercised live are flagged; anything not
# listed here is *assumed* editable, since IsReadOnly on the sgdatum itself
# (checked in apply_edit) is the real, authoritative guard.
COMPUTED_FIELDS = frozenset({"formula", "molecular_weight"})


def field_to_property_type(field):
    if field not in _FIELD_TO_TYPE:
        raise ValueError(
            f"Unknown stoichiometry field {field!r}. Known fields: "
            f"{sorted(_FIELD_TO_TYPE)} (or 'type_N' for an unmapped "
            "SGPropertyType seen via chemdraw_read_stoichiometry_table)."
        )
    return _FIELD_TO_TYPE[field]


def _sgdatum_text(d):
    t = d.find(".//t")
    if t is None:
        return None
    return "".join(s.text or "" for s in t.findall("s"))


def _sgdatum_visible(d):
    t = d.find(".//t")
    if t is None:
        return True
    return t.get("Visible") != "no"


def parse_grids(cdxml_text):
    """Structured, read-only view of every <stoichiometrygrid> in a CDXML
    export. Returns a list of dicts:
        {"grid_index": int, "grid_id": str, "components": [
            {"component_index": int, "structure_ref_id": str | None,
             "is_header": bool, "is_reactant": bool,
             "properties": {property_type_int: {
                 "field": str, "value": str, "text": str | None,
                 "editable": bool, "visible": bool}}}
        ]}
    structure_ref_id is the raw ChemDraw Group ID (matches unit.ID over
    COM, NOT this connector's claude_id string) -- see
    bridge._stoichiometry.read_stoichiometry_tables for the claude_id
    translation layer. None for the row-label header component.
    """
    root = ET.fromstring(cdxml_text)
    grids = []
    for gi, grid in enumerate(root.findall(".//stoichiometrygrid")):
        components = []
        for ci, comp in enumerate(grid.findall("sgcomponent")):
            props = {}
            for d in comp.findall("sgdatum"):
                ptype_raw = d.get("SGPropertyType")
                if ptype_raw is None:
                    continue
                ptype = int(ptype_raw)
                props[ptype] = {
                    "field": SG_PROPERTY_FIELDS.get(ptype, f"type_{ptype}"),
                    "value": d.get("SGDataValue"),
                    "text": _sgdatum_text(d),
                    "editable": d.get("IsReadOnly") != "yes",
                    "visible": _sgdatum_visible(d),
                }
            components.append({
                "component_index": ci,
                "structure_ref_id": comp.get("ComponentReferenceID"),
                "is_header": comp.get("ComponentIsHeader") == "yes",
                "is_reactant": comp.get("ComponentIsReactant") == "yes",
                "properties": props,
            })
        grids.append({
            "grid_index": gi,
            "grid_id": grid.get("id"),
            "components": components,
        })
    return grids


_TAG_RE = re.compile(r'<(/?)(stoichiometrygrid|sgcomponent|sgdatum)\b')
_REF_ID_RE = re.compile(r'ComponentReferenceID="([^"]*)"')
_PROPERTY_TYPE_RE = re.compile(r'SGPropertyType="(\d+)"')
_READONLY_RE = re.compile(r'IsReadOnly="yes"')


def _find_sgdatum_span(text, grid_index, structure_ref_id, property_type):
    """Locate the exact character span of one <sgdatum>...</sgdatum> block
    in the RAW text -- not a reserialized tree, so everything outside the
    target span (including the CDXML DOCTYPE/prolog, which
    xml.etree.ElementTree.tostring silently drops) is preserved
    byte-for-byte.

    Identified by (grid_index, structure_ref_id, property_type) rather
    than the sgdatum's own "id" attribute: confirmed live that id is NOT
    globally unique across different stoichiometrygrid instances in the
    same document (a position/hash-derived number, reused verbatim across
    separate grids built from the same 2-structure selection pattern), so
    id alone is not a safe lookup key -- this positional tag-stream scan
    is, since GetData's serialization order is deterministic document
    order and sgdatum elements never nest.

    Returns (start, end) character offsets, or None if not found.
    """
    cur_grid = -1
    cur_ref = None
    for m in _TAG_RE.finditer(text):
        closing, tag = m.group(1), m.group(2)
        if tag == "stoichiometrygrid" and not closing:
            cur_grid += 1
            cur_ref = None
            continue
        if cur_grid != grid_index:
            continue
        if tag == "sgcomponent" and not closing:
            tag_end = text.index(">", m.end())
            ref_match = _REF_ID_RE.search(text, m.end(), tag_end)
            cur_ref = ref_match.group(1) if ref_match else None
        elif tag == "sgdatum" and not closing and cur_ref == structure_ref_id:
            tag_end = text.index(">", m.end())
            ptype_match = _PROPERTY_TYPE_RE.search(text, m.end(), tag_end)
            if ptype_match and int(ptype_match.group(1)) == property_type:
                close = text.index("</sgdatum>", tag_end) + len("</sgdatum>")
                return m.start(), close
    return None


def apply_edit(cdxml_text, grid_index, structure_ref_id, property_type,
                new_value, new_display_text):
    """Return a new CDXML text with one sgdatum's SGDataValue and visible
    numeric text run (the first <s>...</s> inside it -- the second, the
    unit suffix like "g"/"mmol"/"%", is left untouched) replaced. Raises
    ValueError if the target isn't found or is marked IsReadOnly (a
    ChemDraw-computed field, per the sgdatum's own attribute -- the
    authoritative guard, not just COMPUTED_FIELDS' short-list).

    CONFIRMED LIVE, the hard way: setting SGDataValue alone is not enough
    -- on a sgdatum with no IsEdited attribute at all (the normal state of
    every field on a freshly COM-created grid before any human/tool ever
    types into it), ChemDraw's own loader silently discards the edited
    value and recomputes it from scratch on open (back to 0), while the
    identical edit on a field that already carried IsEdited="yes" (from a
    real prior UI edit) was respected and correctly cascaded on reopen.
    ChemDraw evidently treats IsEdited="yes" as the marker for "this is
    real user input, anchor calculations on it" and everything else as
    "derived, recompute me" -- so every edit here also stamps
    IsEdited="yes" onto the sgdatum, matching what ChemDraw itself writes
    when a human types into that cell."""
    span = _find_sgdatum_span(cdxml_text, grid_index, structure_ref_id,
                              property_type)
    if span is None:
        field = SG_PROPERTY_FIELDS.get(property_type, f"type_{property_type}")
        raise ValueError(
            f"No sgdatum found for grid_index={grid_index}, "
            f"structure_ref_id={structure_ref_id!r}, field={field!r} "
            f"(property_type={property_type}). Call "
            "chemdraw_read_stoichiometry_table first to confirm the "
            "grid/structure actually has this field."
        )
    start, end = span
    block = cdxml_text[start:end]
    opening_tag_end = block.index(">") + 1
    if _READONLY_RE.search(block[:opening_tag_end]):
        field = SG_PROPERTY_FIELDS.get(property_type, f"type_{property_type}")
        raise ValueError(
            f"{field!r} (property_type={property_type}) is marked "
            "IsReadOnly by ChemDraw itself -- it's computed from other "
            "fields (e.g. formula/molecular_weight come from the "
            "structure), not directly editable."
        )
    new_block = re.sub(r'SGDataValue="[^"]*"',
                       f'SGDataValue="{new_value}"', block, count=1)
    new_block = re.sub(r'(<s\b[^>]*>)[^<]*(</s>)',
                       rf'\g<1>{new_display_text}\g<2>', new_block, count=1)
    new_block = _ensure_is_edited(new_block)
    return cdxml_text[:start] + new_block + cdxml_text[end:]


def _ensure_is_edited(block):
    """Stamp IsEdited="yes" onto a sgdatum's opening tag -- added if
    missing entirely, flipped to "yes" if present as something else."""
    tag_end = block.index(">") + 1
    tag = block[:tag_end]
    if 'IsEdited="yes"' in tag:
        return block
    if re.search(r'IsEdited="[^"]*"', tag):
        tag = re.sub(r'IsEdited="[^"]*"', 'IsEdited="yes"', tag)
    else:
        tag = tag[:-1] + '\n IsEdited="yes"' + tag[-1]
    return tag + block[tag_end:]


def apply_edits(cdxml_text, edits):
    """Apply a batch of edits sequentially, each a dict with keys
    grid_index/structure_ref_id/property_type/new_value/new_display_text
    (same shape as apply_edit's args). Threading the text through one
    edit at a time (each a fresh _find_sgdatum_span scan on the
    just-updated text) keeps every offset correct even when a new value's
    string length differs from the old one's -- no stale-offset math
    needed. Returns the final text."""
    for edit in edits:
        cdxml_text = apply_edit(
            cdxml_text,
            edit["grid_index"],
            edit["structure_ref_id"],
            edit["property_type"],
            edit["new_value"],
            edit["new_display_text"],
        )
    return cdxml_text
