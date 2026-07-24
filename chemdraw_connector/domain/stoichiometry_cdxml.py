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
name rather than guessed at.

PRODUCT-SIDE fields (15-22) were a separate, later investigation --
2026-07-23, live probe against a real reactant-left/product-right/arrow-
between scheme (ethanol + benzene -> phenyl acetate). CRITICAL FINDING that
motivated this second pass: a product component uses a COMPLETELY DIFFERENT
SGPropertyType numbering than a reactant one -- there is no single global
"Sample Mass" code shared by both roles. The two share only type 6
(Equivalents). ComponentIsReactant itself is absent entirely (not "no") on
a genuine product component; parse_grids's `comp.get("ComponentIsReactant")
== "yes"` already handles that correctly (`None == "yes"` is False) -- the
long-standing "everything reads back as a reactant" bug was never in this
parsing code, it was in bridge/_stoichiometry.py drawing its own arrow
below the combined bounding box of every given structure instead of
between a reactant cluster and a product cluster (see that module's
make_stoichiometry_table for the fix and the full geometry rationale).

Confirmed live, two separate trials (23.035g/68.075g at exactly the
theoretical 1:1 ratio, then again at 10.0g/20.0g -- deliberately NOT at the
theoretical ratio the second time, to break the degeneracy where every
hypothesis looks identical at 100% yield):
  - type 17 (editable) is the input: set to 20.0, read back as 20.0.
  - type 19 (IsReadOnly) exactly mirrored type 17 in both trials (68.075
    both times, then 20 both times) -- a read-only display copy of
    whatever was typed into 17, not an independently-computed value.
  - type 20 (IsReadOnly) was 20/136.15 = 0.146897 in the second trial --
    exactly the moles implied by type 17's mass over the structure's own
    MW (property_type 2), i.e. genuinely computed, not just mirrored.
  - types 15/16 (IsReadOnly) stayed "0" in BOTH trials despite the
    limiting reactant's own sample_mass being set to a real nonzero value
    beforehand (so a theoretical-yield calculation had a real basis to
    work from) -- never observed to populate via the edit-CDXML-and-
    reopen write path this connector uses. The reactant-side "Limit
    Moles" field (type 5) shows the exact same stuck-at-zero symptom on
    every component regardless of role, suggesting a shared root cause:
    whatever ChemDraw computation these depend on may only run inside the
    live interactive table engine (recalculated as a human edits a cell
    in the UI), not during the on-file-load recompute pass that picks up
    every other edited SGDataValue correctly. Named here as a working
    hypothesis (LOW confidence) rather than left as type_15/type_16,
    since "theoretical yield mass/moles" is the only chemically sensible
    pair of read-only fields a product row would need beyond what 17/19/
    20 already cover -- but treat any value read from these as suspect
    until this connector's own write path is confirmed to make them move.
  - type 18 (editable, %) and type 22 (editable, %) both stayed at their
    "1" (100%) default in both trials and were never observed to actually
    recompute from a real, non-100%, actual-vs-theoretical yield -- same
    caveat as 15/16. Named by position/shape only (an editable "%" field
    sitting where a %Yield input belongs, appearing once per component
    right after Equivalents; 22 for products, 8 for the reactant
    equivalent already documented above) -- LOW-MEDIUM confidence, not
    cascade-verified. [SUPERSEDED for type 18 below -- see the
    2026-07-23-part-2 finding.]
  - type 21 (IsReadOnly) stayed "0" in both trials with no working
    hypothesis at all -- left unmapped (type_21) rather than guessed.

CORRECTION, 2026-07-23 (part 2), live-confirmed: type 18 is NOT %Yield --
it's ChemDraw's own "Purity" field. Confirmed straight from ChemDraw
itself, not guessed: a live COM property read via
chemdraw_read_stoichiometry_table on the header component labeled
property_type 18 as `"value": "Purity", "text": "Purity"` verbatim. It also
functions exactly like a purity multiplier, confirmed by cascade (not just
label): with actual_mass (type 17) set to 0.2108 (211mg, a real isolated
mass) and type 18 set to 0.47 (intending "47% yield"), the read-only
actual_mass_display (type 19, "Product Mass" in ChemDraw's UI, previously
documented above as HIGH confidence to mirror type 17 exactly) instead
came back as 0.099076 (99mg) -- i.e. ChemDraw computed
Product Mass = actual_mass x purity = 0.2108 x 0.47 = 0.09908, not
actual_mass unchanged. Setting type 18 back to 1 (100%) restored
actual_mass_display to 0.2108 (211mg) correctly. Meanwhile the genuinely-
labeled %Yield field (type 21, read-only, right after actual_moles) stayed
stuck at "0.00%" throughout this trial too -- reconfirming, independently,
this module's existing type 21/15/16 finding that ChemDraw's real yield
engine never runs on this connector's edit-CDXML-and-reopen write path.
NOW RENAMED to "purity" below (HIGH confidence -- both the live property
label AND the cascade behavior agree). PRACTICAL CONSEQUENCE: writing
anything other than 1 (100%) to "purity" will proportionally scale DOWN
the reported "Product Mass" (actual_mass_display) away from the real
isolated mass typed into actual_mass -- see tools/stoichiometry.py's tool
docstring for the caller-facing warning, and the new
computed_percent_yield field below for how to actually get a %yield
number instead.

This also means type 18's *position* (right after Equivalents, shaped like
a %Yield input) was a misleading guide to its real identity -- so type 22
("product_percent_weight" below), named the same position/shape-only way
by analogy to reactant's type 8 ("percent_weight"), is NOT upgraded by
this finding; it stays LOW-MEDIUM confidence, unverified by any live
cascade of its own. Its naming is at least self-consistent with type 8's
precedent (both "%Weight" mass-fraction-of-mixture fields, a different
concept from Purity/type 18 or the real %Yield/type 21) -- but that is a
naming-consistency argument, not live proof, and should be read with the
same caution as before.

CLIENT-SIDE COMPUTED FIELDS (added 2026-07-23, NOT ChemDraw-native --
never sent to/read from ChemDraw itself): since ChemDraw's own theoretical-
mass/%yield engine (types 15/16/21) is confirmed dead on this write path
(see above, twice over), compute_derived_yield_fields() below derives a
real theoretical_mass and %yield in pure Python from fields that DO
reliably round-trip: theoretical_mass = the limiting reagent's own
reactant_moles (type 13) x the product's own molecular_weight (type 2);
percent_yield = the product's actual_mass (type 17) / that
theoretical_mass. See that function's docstring for its bail-out
conditions (multiple/zero limiting reagents, missing MW, etc.) -- it never
raises and never guesses past what the data actually supports.
"""
import re
import xml.etree.ElementTree as ET

SG_PROPERTY_FIELDS = {
    1: "formula",              # HIGH -- matches the structure's own formula text
    2: "molecular_weight",     # HIGH -- matches "46.07" / "78.11"
    4: "limiting_reagent",     # HIGH -- matches "Yes" / "No" (reactant-only; absent on product components)
    5: "limit_moles",          # HIGH name, but see module docstring -- never observed to actually populate via this connector's write path
    6: "equivalents",          # HIGH -- confirmed live: visible+populated ("1.06") for a non-limiting reagent; the one field genuinely shared by both reactant and product components
    7: "sample_mass",          # HIGH -- the field actually live-edited; confirmed to cascade correctly (reactant-only)
    8: "percent_weight",       # MEDIUM -- hidden "100.00%" default, inferred from "%" suffix (reactant-only)
    9: "volume",               # MEDIUM -- hidden "0.00 mL" default, inferred from "mL" suffix (reactant-only)
    10: "molarity",            # MEDIUM -- hidden "0.00 M" default, inferred from "M" suffix (reactant-only)
    12: "density",             # MEDIUM -- hidden "0.00 g/mL" default, inferred from "g/mL" suffix (reactant-only)
    13: "reactant_moles",      # HIGH -- matches visible "108.53mmol" / "217.07mmol" (reactant-only)
    14: "reactant_mass",       # HIGH -- matches visible "5.00g" / "10.00g" (reactant-only)
    # Product-side fields (component has no ComponentIsReactant attribute
    # at all). See module docstring's 2026-07-23 investigation for the
    # live evidence and confidence behind each of these.
    15: "theoretical_mass",    # LOW -- plausible role, never observed to populate (product-only)
    16: "theoretical_moles",   # LOW -- plausible role, never observed to populate (product-only)
    17: "actual_mass",         # HIGH -- the field actually live-edited; value read back unchanged, exactly as typed (product-only)
    18: "purity",              # HIGH -- RENAMED 2026-07-23 from "percent_yield": confirmed live to be ChemDraw's own "Purity" field (its property text reads back literally "Purity"), and confirmed by cascade to be a multiplier against actual_mass feeding actual_mass_display/"Product Mass" (0.2108 actual_mass x 0.47 purity -> 0.099076 actual_mass_display). NOT a yield input -- see module docstring's 2026-07-23-part-2 finding and tools/stoichiometry.py's tool docstring for the write-path warning. (product-only)
    19: "actual_mass_display", # HIGH that it mirrors 17 exactly (confirmed live, twice) UNLESS purity (type 18) is set away from 1 (100%), in which case it's actual_mass * purity (confirmed live, see type 18's note); MEDIUM on why a separate read-only copy exists at all (product-only)
    20: "actual_moles",        # HIGH -- confirmed live: exactly actual_mass / molecular_weight (product-only)
    22: "product_percent_weight",  # LOW-MEDIUM -- editable "%" field defaulting to 100%, same shape as reactant's percent_weight (type 8); naming is self-consistent with that precedent but NOT independently cascade-verified -- type 18's 2026-07-23 correction shows position/shape alone is not reliable evidence for this property range, so this one is deliberately NOT upgraded by that finding (product-only)
}
_FIELD_TO_TYPE = {v: k for k, v in SG_PROPERTY_FIELDS.items()}

# Fields ChemDraw itself computes from chemistry/other fields -- rejecting
# an edit here up front (in field_to_property_type) is friendlier than
# silently writing SGDataValue on a field ChemDraw will just recompute or
# ignore. Only the ones actually exercised live are flagged; anything not
# listed here is *assumed* editable, since IsReadOnly on the sgdatum itself
# (checked in apply_edit) is the real, authoritative guard.
COMPUTED_FIELDS = frozenset({
    "formula", "molecular_weight",
    "theoretical_mass", "theoretical_moles",
    "actual_mass_display", "actual_moles",
})


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


def _float_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def compute_derived_yield_fields(grid):
    """CONNECTOR-COMPUTED (NOT a ChemDraw-native value -- never read from or
    written back to ChemDraw itself), real theoretical_mass/%yield for
    every PRODUCT component in one grid (a dict from parse_grids'
    "components" list, i.e. one "grid" entry from parse_grids(...)'s
    return value).

    Exists because ChemDraw's own theoretical-mass/%yield fields (types
    15/16/21) are confirmed dead on this connector's edit-CDXML-and-reopen
    write path -- see this module's docstring -- so a caller who trusts
    them silently gets "0" forever. This computes the same chemistry in
    pure Python instead, from fields that DO reliably round-trip:

        computed_theoretical_mass = limiting_reagent.reactant_moles (type 13)
                                     * product.molecular_weight (type 2)
        computed_percent_yield    = product.actual_mass (type 17)
                                     / computed_theoretical_mass

    Operates on ONE grid only and never looks at any other grid -- a
    document with multiple stoichiometry grids gets one independent
    computation per grid, never cross-referencing reactants from a
    different grid into a different grid's product.

    Returns {component_index: {"computed_theoretical_mass": float | None,
    "computed_percent_yield": float | None, "reason": str | None}}, one
    entry per product component (is_header False, is_reactant False) in
    the grid. Never raises. Both values are None with "reason" explaining
    why whenever the single-limiting-reagent assumption this needs doesn't
    hold for the grid as a whole (zero or more than one reactant component
    flagged limiting_reagent=="Yes", or the limiting reagent has no
    parseable reactant_moles), or per-product whenever that product itself
    has no parseable molecular_weight or actual_mass. A grid with no
    product components at all returns {}.
    """
    reactant_comps = [c for c in grid["components"]
                       if not c["is_header"] and c["is_reactant"]]
    product_comps = [c for c in grid["components"]
                      if not c["is_header"] and not c["is_reactant"]]
    if not product_comps:
        return {}

    def _bail(reason):
        return {p["component_index"]: {
            "computed_theoretical_mass": None,
            "computed_percent_yield": None,
            "reason": reason,
        } for p in product_comps}

    # SGPropertyType 4's raw SGDataValue is "1"/"0" (see
    # SG_PROPERTY_FIELDS[4] and _sgdatum_text) -- "Yes"/"No" is only the
    # rendered display TEXT, never the raw value. Comparing against
    # "value" == "Yes" here was confirmed live to always be False (a
    # genuinely limiting reactant's raw value is "1"), silently bailing
    # out on every real grid regardless of whether a limiting reagent was
    # actually flagged.
    limiting = [c for c in reactant_comps
                if c["properties"].get(4, {}).get("value") == "1"]
    if len(limiting) != 1:
        if not limiting:
            reason = ("no reactant component is flagged limiting_reagent "
                       "(SGPropertyType 4 raw value == '1')")
        else:
            reason = (f"{len(limiting)} reactant components are flagged "
                      "limiting_reagent -- expected exactly 1")
        return _bail(reason)

    limiting_moles = _float_or_none(
        limiting[0]["properties"].get(13, {}).get("value"))
    if limiting_moles is None:
        return _bail(
            "limiting reagent has no parseable reactant_moles "
            "(SGPropertyType 13)"
        )

    out = {}
    for p in product_comps:
        mw = _float_or_none(p["properties"].get(2, {}).get("value"))
        if mw is None:
            out[p["component_index"]] = {
                "computed_theoretical_mass": None,
                "computed_percent_yield": None,
                "reason": ("product has no parseable molecular_weight "
                           "(SGPropertyType 2)"),
            }
            continue
        theoretical_mass = limiting_moles * mw
        actual_mass = _float_or_none(p["properties"].get(17, {}).get("value"))
        if actual_mass is None:
            out[p["component_index"]] = {
                "computed_theoretical_mass": theoretical_mass,
                "computed_percent_yield": None,
                "reason": ("product has no parseable actual_mass "
                           "(SGPropertyType 17)"),
            }
            continue
        if theoretical_mass == 0:
            out[p["component_index"]] = {
                "computed_theoretical_mass": theoretical_mass,
                "computed_percent_yield": None,
                "reason": "computed_theoretical_mass is 0 -- can't divide",
            }
            continue
        out[p["component_index"]] = {
            "computed_theoretical_mass": theoretical_mass,
            "computed_percent_yield": actual_mass / theoretical_mass,
            "reason": None,
        }
    return out


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
