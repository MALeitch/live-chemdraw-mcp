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


def test_hidden_property_reports_visible_false():
    text = CDXML_ONE_GRID.replace(
        '<objecttag id="157"><t p="0 0">',
        '<objecttag id="157"><t p="0 0" Visible="no">',
    )
    grids = sc.parse_grids(text)
    assert grids[0]["components"][1]["properties"][7]["visible"] is False
