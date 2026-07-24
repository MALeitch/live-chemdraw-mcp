import re

import pytest

from chemdraw_connector.bridge import _stoichiometry as bs
from tools import stoichiometry as stoich_tool


def test_format_value_drops_trailing_zero_for_whole_numbers():
    assert bs._format_value(5) == "5"
    assert bs._format_value(5.0) == "5"
    assert bs._format_value("10") == "10"


def test_format_value_keeps_decimals_for_fractional_numbers():
    assert bs._format_value(1.0719) == repr(1.0719)
    assert bs._format_value(2.5) == repr(2.5)


def test_write_base_name_strips_extension():
    assert bs._write_base_name("my-doc.cdxml") == "my-doc"


def test_write_base_name_strips_one_prior_stoich_suffix():
    name = "my-doc-stoich-20260722-072050-4015b8.cdxml"
    assert bs._write_base_name(name) == "my-doc"


def test_write_base_name_strips_chained_prior_stoich_suffixes():
    """Reproduces the real bug: editing the same document repeatedly feeds
    each edit's new_active_document (already suffixed) back in as the next
    edit's doc.name -- confirmed live to otherwise accumulate
    "-stoich-...-stoich-..." onto the filename forever."""
    name = ("my-doc-stoich-20260722-072050-4015b8"
            "-stoich-20260722-072127-73813f.cdxml")
    assert bs._write_base_name(name) == "my-doc"


def test_write_base_name_sanitizes_unsafe_characters():
    assert bs._write_base_name("Untitled ACS Document 1996-6") == \
        "Untitled_ACS_Document_1996-6"


# ---------------------------------------------------------------------------
# _STOICH_SUFFIX_RE: the same regex edit_stoichiometry_table uses to decide
# whether the document it's about to edit is itself a connector-generated
# throwaway from a PRIOR edit (safe to auto-close) vs. the user's original
# file (never auto-closed). Tested against the extension-stripped base name,
# matching how edit_stoichiometry_table's go() actually applies it.
# ---------------------------------------------------------------------------

def test_stoich_suffix_re_matches_a_generated_throwaway_name():
    assert bs._STOICH_SUFFIX_RE.search("foo-stoich-20260724-153000-a1b2c3")


def test_stoich_suffix_re_rejects_a_plain_document_name():
    assert not bs._STOICH_SUFFIX_RE.search("foo")
    assert not bs._STOICH_SUFFIX_RE.search("foo-stoichiometric")


# ---------------------------------------------------------------------------
# read_stoichiometry_tables: verifies the computed_theoretical_mass/
# computed_percent_yield merge actually reaches the tool's output shape --
# NOT via a live ChemDraw connection (forbidden while the user has a real
# unsaved document open), only via monkeypatching the CDXML export call
# _Stoichiometry.read_stoichiometry_tables depends on and stubbing out the
# other bridge plumbing it needs (_doc/_cache_for/_run/_raw_id_map).
# ---------------------------------------------------------------------------

_CDXML_WITH_LIMITING_REAGENT_AND_PRODUCT = """<?xml version="1.0" ?>
<!DOCTYPE CDXML SYSTEM "https://static.chemistry.revvitycloud.com/cdxml/CDXML.dtd">
<CDXML Name="test.cdxml">
<stoichiometrygrid id="44">
 <sgcomponent id="1" ComponentIsReactant="yes" ComponentIsHeader="yes">
  <sgdatum id="1000" SGDataType="4" SGDataValue="Sample Mass" SGPropertyType="7" IsReadOnly="yes">
   <objecttag id="1"><t p="0 0"><s>Sample Mass</s></t></objecttag>
  </sgdatum>
 </sgcomponent>
 <sgcomponent id="3" ComponentReferenceID="8" ComponentIsReactant="yes">
  <sgdatum id="131840" SGDataType="3" SGDataValue="46.069" SGPropertyType="2" IsReadOnly="yes">
   <objecttag id="153"><t p="0 0"><s>46.07</s></t></objecttag>
  </sgdatum>
  <sgdatum id="131900" SGDataType="4" SGDataValue="1" SGPropertyType="4" IsReadOnly="yes">
   <objecttag id="154"><t p="0 0"><s>Yes</s></t></objecttag>
  </sgdatum>
  <sgdatum id="131950" SGDataType="3" SGDataValue="0.14690" SGPropertyType="13" IsReadOnly="yes">
   <objecttag id="155"><t p="0 0"><s>146.90</s><s>mmol</s></t></objecttag>
  </sgdatum>
 </sgcomponent>
 <sgcomponent id="5" ComponentReferenceID="23">
  <sgdatum id="132352" SGDataType="3" SGDataValue="136.15" SGPropertyType="2" IsReadOnly="yes">
   <objecttag id="97"><t p="0 0"><s>136.15</s></t></objecttag>
  </sgdatum>
  <sgdatum id="1115392" SGDataType="3" SGDataValue="20" SGPropertyType="17">
   <objecttag id="103"><t p="0 0"><s>20.00</s><s>g</s></t></objecttag>
  </sgdatum>
  <sgdatum id="1115400" SGDataType="3" SGDataValue="1" SGPropertyType="18">
   <objecttag id="104"><t p="0 0"><s>100.00</s><s>%</s></t></objecttag>
  </sgdatum>
 </sgcomponent>
</stoichiometrygrid>
</CDXML>
"""


class _FakeStoichBridge(bs._Stoichiometry):
    """Exercises the real read_stoichiometry_tables method with just enough
    stubbed plumbing to run outside a live ChemDraw session -- _doc/
    _cache_for/_run/_raw_id_map are the only hooks that method calls beyond
    the CDXML text itself (which is monkeypatched at the module-level
    snapshots.export_cdxml_text call site instead, the real seam)."""
    def _doc(self):
        return object()

    def _cache_for(self, doc):
        return {}

    def _run(self, fn, timeout=None):
        return fn()

    def _raw_id_map(self, doc, cache):
        return {}


def test_read_stoichiometry_tables_attaches_computed_yield_fields(monkeypatch):
    monkeypatch.setattr(
        bs.snapshots, "export_cdxml_text",
        lambda doc: _CDXML_WITH_LIMITING_REAGENT_AND_PRODUCT,
    )
    bridge = _FakeStoichBridge()
    result = bridge.read_stoichiometry_tables()

    grid = result["grids"][0]
    product = next(c for c in grid["components"]
                    if not c["is_header"] and not c["is_reactant"])
    props = product["properties"]

    theoretical = props["computed_theoretical_mass"]
    yield_ = props["computed_percent_yield"]
    assert theoretical["value"] == pytest.approx(0.14690 * 136.15)
    assert yield_["value"] == pytest.approx(20.0 / (0.14690 * 136.15))
    assert theoretical["reason"] is None
    assert yield_["reason"] is None
    for field in (theoretical, yield_):
        assert field["connector_computed"] is True
        assert field["property_type"] is None
        assert field["editable"] is False

    # Native ChemDraw fields (like purity) are untouched by the merge.
    assert props["purity"]["property_type"] == 18


# ---------------------------------------------------------------------------
# tool docstring accuracy (tools/stoichiometry.py) -- registered against a
# fake FastMCP stand-in, no bridge calls actually made.
# ---------------------------------------------------------------------------

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = {
                "fn": fn,
                "description": kwargs.get("description"),
            }
            return fn
        return deco


def test_tool_docstrings_describe_purity_correctly_and_drop_percent_yield():
    mcp = _FakeMCP()
    stoich_tool.register(mcp, bridge=object())

    read_doc = mcp.tools["chemdraw_read_stoichiometry_table"]["fn"].__doc__
    assert "purity" in read_doc
    assert "not a yield input" in read_doc.lower() or \
        "not a yield" in read_doc.lower()
    assert "computed_theoretical_mass" in read_doc
    assert "computed_percent_yield" in read_doc

    make_desc = mcp.tools["chemdraw_make_stoichiometry_table"]["description"]
    edit_desc = mcp.tools["chemdraw_edit_stoichiometry_table"]["description"]
    # "percent_yield" as its own field name (the pre-rename name) must be
    # gone everywhere -- but "computed_percent_yield" legitimately contains
    # that substring, so check word-boundaries, not bare substring absence.
    stale_name_re = re.compile(r"(?<!computed_)\bpercent_yield\b")
    assert not stale_name_re.search(make_desc)
    assert not stale_name_re.search(edit_desc or "")
    assert not stale_name_re.search(read_doc)
    assert "purity" in make_desc
