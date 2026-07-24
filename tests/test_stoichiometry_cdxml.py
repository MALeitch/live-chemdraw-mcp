import pytest

from chemdraw_connector.domain import stoichiometry_cdxml as sc


def _grid(sample_mass_id="459520", sample_mass_value="5", sample_mass_text="5.00"):
    """One <stoichiometrygrid> with a header (label) component plus one
    real reactant component -- mirrors the real shape confirmed live
    (ComponentIsHeader="yes" carries literal row-label text, not data;
    Formula/MW are IsReadOnly="yes"; Sample Mass is the field actually
    live-edited and confirmed to cascade)."""
    return f"""<stoichiometrygrid id="24">
 <sgcomponent id="1" ComponentIsReactant="yes" ComponentIsHeader="yes">
  <sgdatum id="1000" SGDataType="4" SGDataValue="Sample Mass" SGPropertyType="7" IsReadOnly="yes">
   <objecttag id="1"><t p="0 0"><s>Sample Mass</s></t></objecttag>
  </sgdatum>
 </sgcomponent>
 <sgcomponent id="3" ComponentReferenceID="8" ComponentIsReactant="yes">
  <sgdatum id="131840" SGDataType="3" SGDataValue="46.069" SGPropertyType="2" IsReadOnly="yes">
   <objecttag id="153"><t p="0 0"><s>46.07</s></t></objecttag>
  </sgdatum>
  <sgdatum id="{sample_mass_id}" SGDataType="3" SGDataValue="{sample_mass_value}" SGPropertyType="7" IsEdited="yes">
   <objecttag id="157"><t p="0 0"><s>{sample_mass_text}</s><s>g</s></t></objecttag>
  </sgdatum>
 </sgcomponent>
</stoichiometrygrid>"""


CDXML_ONE_GRID = f"""<?xml version="1.0" ?>
<!DOCTYPE CDXML SYSTEM "https://static.chemistry.revvitycloud.com/cdxml/CDXML.dtd">
<CDXML Name="test.cdxml">
{_grid()}
</CDXML>
"""

# Two grids reusing the EXACT SAME sgdatum "id" values -- confirmed live
# behavior (a position/hash-derived number, not a globally unique key)
# that _find_sgdatum_span must be immune to.
CDXML_TWO_GRIDS_SAME_IDS = f"""<?xml version="1.0" ?>
<!DOCTYPE CDXML SYSTEM "https://static.chemistry.revvitycloud.com/cdxml/CDXML.dtd">
<CDXML Name="test.cdxml">
{_grid(sample_mass_value="0", sample_mass_text="0.00")}
{_grid(sample_mass_value="10", sample_mass_text="10.00")}
</CDXML>
"""


def _grid_with_product(product_ref_id="23"):
    """A reactant component plus a genuine PRODUCT component -- mirrors the
    real shape confirmed live 2026-07-23 (see domain/stoichiometry_cdxml.py's
    module docstring): a product component carries NO ComponentIsReactant
    attribute at all (not "no" -- simply absent), and uses a completely
    different SGPropertyType range (15-22) than a reactant component."""
    return f"""<stoichiometrygrid id="44">
 <sgcomponent id="1" ComponentIsReactant="yes" ComponentIsHeader="yes">
  <sgdatum id="1000" SGDataType="4" SGDataValue="Sample Mass" SGPropertyType="7" IsReadOnly="yes">
   <objecttag id="1"><t p="0 0"><s>Sample Mass</s></t></objecttag>
  </sgdatum>
 </sgcomponent>
 <sgcomponent id="3" ComponentReferenceID="8" ComponentIsReactant="yes">
  <sgdatum id="131840" SGDataType="3" SGDataValue="46.069" SGPropertyType="2" IsReadOnly="yes">
   <objecttag id="153"><t p="0 0"><s>46.07</s></t></objecttag>
  </sgdatum>
 </sgcomponent>
 <sgcomponent id="5" ComponentReferenceID="{product_ref_id}">
  <sgdatum id="132352" SGDataType="3" SGDataValue="136.15" SGPropertyType="2" IsReadOnly="yes">
   <objecttag id="97"><t p="0 0"><s>136.15</s></t></objecttag>
  </sgdatum>
  <sgdatum id="1115392" SGDataType="3" SGDataValue="20" SGPropertyType="17">
   <objecttag id="103"><t p="0 0"><s>20.00</s><s>g</s></t></objecttag>
  </sgdatum>
  <sgdatum id="1246464" SGDataType="3" SGDataValue="20" SGPropertyType="19" IsReadOnly="yes">
   <objecttag id="105"><t p="0 0"><s>20.00</s><s>g</s></t></objecttag>
  </sgdatum>
  <sgdatum id="1312000" SGDataType="3" SGDataValue="0.146897" SGPropertyType="20" IsReadOnly="yes">
   <objecttag id="106"><t p="0 0"><s>146.90</s><s>mmol</s></t></objecttag>
  </sgdatum>
 </sgcomponent>
</stoichiometrygrid>"""


CDXML_WITH_PRODUCT = f"""<?xml version="1.0" ?>
<!DOCTYPE CDXML SYSTEM "https://static.chemistry.revvitycloud.com/cdxml/CDXML.dtd">
<CDXML Name="test.cdxml">
{_grid_with_product()}
</CDXML>
"""


def test_parse_grids_reads_product_component_as_not_reactant():
    """The actual bug this connector shipped with: a genuine product
    component has no ComponentIsReactant attribute, and `.get(...) ==
    "yes"` must resolve that absence to False, not silently default to
    True. This was never actually a parsing bug -- see bridge/
    _stoichiometry.py for where the real bug (arrow placement) lived --
    but it's the contract this parser must keep now that a real product
    component is reachable at all."""
    grids = sc.parse_grids(CDXML_WITH_PRODUCT)
    header, reactant, product = grids[0]["components"]
    assert reactant["is_reactant"] is True
    assert product["is_reactant"] is False
    assert product["is_header"] is False
    assert product["structure_ref_id"] == "23"


def test_parse_grids_maps_product_side_property_types():
    grids = sc.parse_grids(CDXML_WITH_PRODUCT)
    product = grids[0]["components"][2]
    props = product["properties"]
    assert props[17]["field"] == "actual_mass"
    assert props[17]["value"] == "20"
    assert props[17]["editable"] is True
    assert props[19]["field"] == "actual_mass_display"
    assert props[19]["value"] == "20"
    assert props[19]["editable"] is False
    assert props[20]["field"] == "actual_moles"
    assert props[20]["value"] == "0.146897"
    assert props[20]["editable"] is False


def test_field_to_property_type_round_trips_product_fields():
    assert sc.field_to_property_type("actual_mass") == 17
    assert sc.field_to_property_type("actual_moles") == 20
    assert sc.field_to_property_type("purity") == 18


def test_parse_grids_skips_header_and_reads_reactant_properties():
    grids = sc.parse_grids(CDXML_ONE_GRID)
    assert len(grids) == 1
    grid = grids[0]
    assert grid["grid_id"] == "24"
    assert len(grid["components"]) == 2

    header, reactant = grid["components"]
    assert header["is_header"] is True
    assert header["structure_ref_id"] is None

    assert reactant["is_header"] is False
    assert reactant["structure_ref_id"] == "8"
    props = reactant["properties"]
    assert props[2]["field"] == "molecular_weight"
    assert props[2]["value"] == "46.069"
    assert props[2]["editable"] is False  # IsReadOnly="yes"
    assert props[7]["field"] == "sample_mass"
    assert props[7]["value"] == "5"
    assert props[7]["text"] == "5.00g"
    assert props[7]["editable"] is True


def test_unmapped_property_type_gets_synthetic_field_name():
    text = CDXML_ONE_GRID.replace('SGPropertyType="2"', 'SGPropertyType="99"')
    grids = sc.parse_grids(text)
    reactant = grids[0]["components"][1]
    assert reactant["properties"][99]["field"] == "type_99"


def test_field_to_property_type_round_trips_known_fields():
    assert sc.field_to_property_type("sample_mass") == 7
    assert sc.field_to_property_type("reactant_moles") == 13
    with pytest.raises(ValueError):
        sc.field_to_property_type("not_a_real_field")


def test_apply_edit_updates_value_and_visible_text_only_first_span():
    new_text = sc.apply_edit(
        CDXML_ONE_GRID, grid_index=0, structure_ref_id="8",
        property_type=7, new_value="10", new_display_text="10.00",
    )
    grids = sc.parse_grids(new_text)
    props = grids[0]["components"][1]["properties"]
    assert props[7]["value"] == "10"
    assert props[7]["text"] == "10.00g"  # unit suffix "g" untouched
    # Untouched fields survive unrelated edits.
    assert props[2]["value"] == "46.069"


def test_apply_edit_stamps_is_edited_when_absent():
    """Confirmed live: a sgdatum with no IsEdited attribute at all (every
    field's normal state on a freshly COM-created grid, before anyone
    types into it) has its edited SGDataValue silently discarded by
    ChemDraw's own loader unless IsEdited="yes" is also stamped on."""
    text_no_is_edited = CDXML_ONE_GRID.replace(' IsEdited="yes"', "")
    assert 'IsEdited="yes"' not in text_no_is_edited  # sanity-check the fixture edit
    new_text = sc.apply_edit(
        text_no_is_edited, grid_index=0, structure_ref_id="8",
        property_type=7, new_value="10", new_display_text="10.00",
    )
    span = sc._find_sgdatum_span(new_text, 0, "8", 7)
    block = new_text[span[0]:span[1]]
    assert 'IsEdited="yes"' in block[:block.index(">") + 1]


def test_apply_edit_rejects_readonly_field():
    with pytest.raises(ValueError, match="IsReadOnly"):
        sc.apply_edit(
            CDXML_ONE_GRID, grid_index=0, structure_ref_id="8",
            property_type=2, new_value="99", new_display_text="99.00",
        )


def test_apply_edit_missing_target_raises_clear_error():
    with pytest.raises(ValueError, match="No sgdatum found"):
        sc.apply_edit(
            CDXML_ONE_GRID, grid_index=0, structure_ref_id="does-not-exist",
            property_type=7, new_value="10", new_display_text="10.00",
        )


def test_find_sgdatum_span_is_immune_to_ids_reused_across_grids():
    """The real live wall this connector's own prior probing hit: sgdatum
    "id" values are reused verbatim across separate stoichiometrygrid
    instances built from the same 2-structure pattern. A lookup keyed on
    id alone would silently edit the WRONG grid's data; grid_index +
    structure_ref_id + property_type must not have that failure mode."""
    grids_before = sc.parse_grids(CDXML_TWO_GRIDS_SAME_IDS)
    assert grids_before[0]["components"][1]["properties"][7]["value"] == "0"
    assert grids_before[1]["components"][1]["properties"][7]["value"] == "10"

    new_text = sc.apply_edit(
        CDXML_TWO_GRIDS_SAME_IDS, grid_index=0, structure_ref_id="8",
        property_type=7, new_value="99", new_display_text="99.00",
    )
    grids_after = sc.parse_grids(new_text)
    # Only grid_index=0 changed.
    assert grids_after[0]["components"][1]["properties"][7]["value"] == "99"
    assert grids_after[1]["components"][1]["properties"][7]["value"] == "10"


def test_apply_edits_batch_threads_text_through_sequentially():
    new_text = sc.apply_edits(CDXML_TWO_GRIDS_SAME_IDS, [
        {"grid_index": 0, "structure_ref_id": "8", "property_type": 7,
         "new_value": "1", "new_display_text": "1.00"},
        {"grid_index": 1, "structure_ref_id": "8", "property_type": 7,
         "new_value": "2", "new_display_text": "2.00"},
    ])
    grids = sc.parse_grids(new_text)
    assert grids[0]["components"][1]["properties"][7]["value"] == "1"
    assert grids[1]["components"][1]["properties"][7]["value"] == "2"


def test_sg_property_fields_type_18_renamed_to_purity():
    """The 2026-07-23 correction: type 18 is ChemDraw's own "Purity"
    field, not %Yield -- confirmed live (see module docstring). The old
    name must be fully gone, not just aliased, since silently accepting
    both names would let a caller keep writing "percent_yield" and hit
    the exact silent-Product-Mass-corruption bug this rename exists to
    prevent."""
    assert sc.SG_PROPERTY_FIELDS[18] == "purity"
    assert "percent_yield" not in sc._FIELD_TO_TYPE


def test_field_to_property_type_rejects_old_percent_yield_name():
    with pytest.raises(ValueError, match="percent_yield"):
        sc.field_to_property_type("percent_yield")


def test_field_to_property_type_round_trips_purity():
    assert sc.field_to_property_type("purity") == 18


# ---------------------------------------------------------------------------
# compute_derived_yield_fields: connector-computed theoretical_mass/%yield,
# built directly from parse_grids-shaped dicts (no CDXML text needed for
# most cases -- this is pure post-parse arithmetic).
# ---------------------------------------------------------------------------

def _component(index, is_header=False, is_reactant=False, properties=None):
    return {
        "component_index": index,
        "structure_ref_id": str(index),
        "is_header": is_header,
        "is_reactant": is_reactant,
        "properties": properties or {},
    }


def _prop(value):
    return {"field": "irrelevant_for_this_test", "value": value,
            "text": None, "editable": True, "visible": True}


def _grid_dict(components):
    return {"grid_index": 0, "grid_id": "1", "components": components}


def test_compute_derived_yield_fields_happy_path():
    grid = _grid_dict([
        _component(0, is_header=True, is_reactant=True),
        _component(1, is_reactant=True, properties={
            4: _prop("Yes"),       # limiting_reagent
            13: _prop("0.14690"),  # reactant_moles (mol)
        }),
        _component(2, is_reactant=True, properties={4: _prop("No")}),
        _component(3, properties={
            2: _prop("136.15"),    # molecular_weight
            17: _prop("0.02108"),  # actual_mass -- deliberately off theoretical
        }),
    ])
    derived = sc.compute_derived_yield_fields(grid)
    result = derived[3]
    assert result["reason"] is None
    assert result["computed_theoretical_mass"] == pytest.approx(0.14690 * 136.15)
    assert result["computed_percent_yield"] == pytest.approx(
        0.02108 / (0.14690 * 136.15))


def test_compute_derived_yield_fields_bails_out_with_no_limiting_reagent():
    grid = _grid_dict([
        _component(1, is_reactant=True, properties={4: _prop("No")}),
        _component(2, properties={2: _prop("100"), 17: _prop("1")}),
    ])
    derived = sc.compute_derived_yield_fields(grid)
    assert derived[2]["computed_theoretical_mass"] is None
    assert derived[2]["computed_percent_yield"] is None
    assert "no reactant component is flagged" in derived[2]["reason"]


def test_compute_derived_yield_fields_bails_out_with_multiple_limiting_reagents():
    grid = _grid_dict([
        _component(1, is_reactant=True, properties={4: _prop("Yes"), 13: _prop("1")}),
        _component(2, is_reactant=True, properties={4: _prop("Yes"), 13: _prop("1")}),
        _component(3, properties={2: _prop("100"), 17: _prop("1")}),
    ])
    derived = sc.compute_derived_yield_fields(grid)
    assert derived[3]["computed_theoretical_mass"] is None
    assert "2 reactant components" in derived[3]["reason"]


def test_compute_derived_yield_fields_bails_out_when_limiting_reagent_has_no_moles():
    grid = _grid_dict([
        _component(1, is_reactant=True, properties={4: _prop("Yes")}),  # no type 13
        _component(2, properties={2: _prop("100"), 17: _prop("1")}),
    ])
    derived = sc.compute_derived_yield_fields(grid)
    assert derived[2]["computed_theoretical_mass"] is None
    assert "reactant_moles" in derived[2]["reason"]


def test_compute_derived_yield_fields_bails_out_when_product_missing_molecular_weight():
    grid = _grid_dict([
        _component(1, is_reactant=True, properties={4: _prop("Yes"), 13: _prop("0.1")}),
        _component(2, properties={17: _prop("1")}),  # no type 2
    ])
    derived = sc.compute_derived_yield_fields(grid)
    assert derived[2]["computed_theoretical_mass"] is None
    assert "molecular_weight" in derived[2]["reason"]


def test_compute_derived_yield_fields_still_reports_theoretical_mass_without_actual_mass():
    grid = _grid_dict([
        _component(1, is_reactant=True, properties={4: _prop("Yes"), 13: _prop("0.1")}),
        _component(2, properties={2: _prop("100")}),  # no type 17
    ])
    derived = sc.compute_derived_yield_fields(grid)
    assert derived[2]["computed_theoretical_mass"] == pytest.approx(10.0)
    assert derived[2]["computed_percent_yield"] is None
    assert "actual_mass" in derived[2]["reason"]


def test_compute_derived_yield_fields_returns_empty_dict_with_no_products():
    grid = _grid_dict([
        _component(1, is_reactant=True, properties={4: _prop("Yes"), 13: _prop("0.1")}),
    ])
    assert sc.compute_derived_yield_fields(grid) == {}


def test_compute_derived_yield_fields_bails_out_cleanly_on_real_fixture():
    """CDXML_WITH_PRODUCT's reactant component never carries a
    limiting_reagent (type 4) property at all -- the realistic case of a
    caller reading a grid that simply doesn't have enough data for this
    computation, not a synthetic edge case."""
    grids = sc.parse_grids(CDXML_WITH_PRODUCT)
    derived = sc.compute_derived_yield_fields(grids[0])
    product = grids[0]["components"][2]
    result = derived[product["component_index"]]
    assert result["computed_theoretical_mass"] is None
    assert result["computed_percent_yield"] is None
    assert result["reason"]


def test_hidden_property_reports_visible_false():
    text = CDXML_ONE_GRID.replace(
        '<objecttag id="157"><t p="0 0">',
        '<objecttag id="157"><t p="0 0" Visible="no">',
    )
    grids = sc.parse_grids(text)
    assert grids[0]["components"][1]["properties"][7]["visible"] is False
