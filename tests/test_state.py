"""build_snapshot's fast-CDXML-vs-live-COM bounds decision — pure, no COM.

Mirrors test_targets_cache.py's fake COM stand-ins (FakeCollection/FakeTag/
FakeUnit) plus a couple of live-bounds properties instrumented with a call
counter, so tests can prove which path (CDXML-derived vs. live per-unit
describe_unit) actually ran rather than just checking the final numbers.
Real CDXML text is fed through the real snapshots.export_cdxml_text /
domain.cdxml_snapshot.index_units, exactly like test_cdxml_snapshot.py's
fixtures, so the indexing itself is exercised too, not re-mocked.
"""
from chemdraw_connector import state, targets


class FakeTag:
    def __init__(self):
        self.StringValue = None
        self.Visible = True
        self.Persistent = False


class FakeCollection:
    """Stands in for an IChemDrawObjects-style collection: 1-based Item()
    and Count."""

    def __init__(self, items=None):
        self._items = list(items or [])

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]

    def append(self, item):
        self._items.append(item)


class FakeObjects:
    """unit.Objects: Atoms/Bonds sub-collections' .Count plus .Formula —
    the live-per-unit properties describe_unit/unit_formula read."""

    def __init__(self, atom_count=0, bond_count=0, formula=""):
        self.Atoms = FakeCollection([object()] * atom_count)
        self.Bonds = FakeCollection([object()] * bond_count)
        self.Formula = formula


class FakeUnit:
    """Stands in for a Group: pre-tagged with a claude_id (so
    targets.ensure_id/get_id resolve it without stamping a new one), plus
    Left/Top/Right/Bottom live-bounds properties that count every access —
    the observable signal that describe_unit's live-COM fallback path ran,
    as opposed to the CDXML-derived fast path, which never touches these."""

    def __init__(self, uid_tag, live_bounds, atom_count=0, bond_count=0, formula=""):
        self.Objects = FakeObjects(atom_count, bond_count, formula)
        self._tags = {}
        self._live_bounds = live_bounds
        self.live_bounds_reads = 0
        if uid_tag is not None:
            tag = FakeTag()
            tag.StringValue = uid_tag
            self._tags["claude_id"] = tag

    def GetObjectTag(self, name):
        return self._tags.get(name)

    def MakeObjectTag(self, name, _persistent_flag):
        tag = FakeTag()
        self._tags[name] = tag
        return tag

    @property
    def Left(self):
        self.live_bounds_reads += 1
        return self._live_bounds["left"]

    @property
    def Top(self):
        self.live_bounds_reads += 1
        return self._live_bounds["top"]

    @property
    def Right(self):
        self.live_bounds_reads += 1
        return self._live_bounds["right"]

    @property
    def Bottom(self):
        self.live_bounds_reads += 1
        return self._live_bounds["bottom"]


class FakeDocObjects:
    """doc.Objects.GetData("text/xml") — the COM half of the CDXML export
    that snapshots.export_cdxml_text wraps."""

    def __init__(self, cdxml_text):
        self._cdxml_text = cdxml_text

    def GetData(self, fmt):
        return self._cdxml_text


class FakeDoc:
    def __init__(self, groups, cdxml_text, width=540.0, height=720.0):
        self.Groups = FakeCollection(groups)
        self.Atoms = FakeCollection([])
        self.Bonds = FakeCollection([])
        self.Width = width
        self.Height = height
        self.Objects = FakeDocObjects(cdxml_text)


# ---------- unique id in the CDXML index -> fast path ----------

CDXML_UNIQUE = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="10" BoundingBox="10.0 20.0 30.0 40.0">
   <fragment id="11" BoundingBox="10.0 20.0 30.0 40.0">
    <n id="12" p="10 20"/>
    <n id="13" p="20 30"/>
    <b id="20" B="12" E="13" Order="1"/>
   </fragment>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-fast"/>
  </group>
 </page>
</CDXML>
"""


def test_unique_id_uses_fast_cdxml_bounds_not_live_com():
    # Live bounds/atom/bond counts are deliberately set to obviously-wrong
    # values that don't match the CDXML fixture, so any leakage from the
    # live path would make these assertions fail loudly.
    unit = FakeUnit(
        "claude-fast",
        live_bounds={"left": 999.0, "top": 999.0, "right": 999.0, "bottom": 999.0},
        atom_count=77, bond_count=66, formula="C2H6",
    )
    doc = FakeDoc([unit], CDXML_UNIQUE)

    out = state.build_snapshot(doc)

    assert unit.live_bounds_reads == 0, \
        "a uniquely-tagged unit's fast path must never touch live Left/Top/Right/Bottom"
    assert len(out) == 1
    entry = out[0]
    assert entry["id"] == "claude-fast"
    assert entry["bounds"] == {"left": 10.0, "top": 20.0, "right": 30.0, "bottom": 40.0}
    assert entry["atom_count"] == 2 and entry["bond_count"] == 1, \
        "fast path must report the CDXML-derived counts, not the live Objects counts"
    assert entry["formula"] == "C2H6", \
        "formula must still be COM-sourced even on the fast bounds path"


# ---------- id missing from the CDXML index entirely -> live fallback ----------

CDXML_OTHER_ONLY = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="30" BoundingBox="0.0 0.0 5.0 5.0">
   <fragment id="31" BoundingBox="0.0 0.0 5.0 5.0">
    <n id="32" p="0 0"/>
   </fragment>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-other"/>
  </group>
 </page>
</CDXML>
"""


def test_id_missing_from_cdxml_index_falls_back_to_live_read():
    unit = FakeUnit(
        "claude-missing",
        live_bounds={"left": 1.1, "top": 2.2, "right": 3.3, "bottom": 4.4},
        atom_count=5, bond_count=6, formula="H2O",
    )
    doc = FakeDoc([unit], CDXML_OTHER_ONLY)

    out = state.build_snapshot(doc)

    assert unit.live_bounds_reads == 4, \
        "describe_unit's fallback must read all four live bounds properties"
    entry = out[0]
    assert entry["id"] == "claude-missing"
    assert entry["bounds"] == {"left": 1.1, "top": 2.2, "right": 3.3, "bottom": 4.4}
    assert entry["atom_count"] == 5 and entry["bond_count"] == 6, \
        "fallback must report the live Objects counts, not any CDXML value"
    assert entry["formula"] == "H2O"


# ---------- id present but CDXML export had no BoundingBox -> live fallback ----------

CDXML_NO_BOUNDS = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="80">
   <fragment id="81">
    <n id="82" p="0 0"/>
   </fragment>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-nobounds"/>
  </group>
 </page>
</CDXML>
"""


def test_cdxml_entry_with_no_bounds_falls_back_to_live_read():
    unit = FakeUnit(
        "claude-nobounds",
        live_bounds={"left": 7.0, "top": 8.0, "right": 9.0, "bottom": 10.0},
        atom_count=3, bond_count=2, formula="NaCl",
    )
    doc = FakeDoc([unit], CDXML_NO_BOUNDS)

    out = state.build_snapshot(doc)

    assert unit.live_bounds_reads == 4
    entry = out[0]
    assert entry["bounds"] == {"left": 7.0, "top": 8.0, "right": 9.0, "bottom": 10.0}
    assert entry["atom_count"] == 3 and entry["bond_count"] == 2
    assert entry["formula"] == "NaCl"


# ---------- two units sharing one id (sibling-fragment collision) ----------
#
# targets.iter_units already guarantees distinct ids among the groups it
# retags on collision (see test_targets_cache.py), so reproducing this via a
# real doc.Groups scan isn't possible/representative here. This test targets
# build_snapshot's own id_counts[uid] == 1 guard directly by handing it a
# units list -- via a monkeypatched targets.iter_units -- that already
# contains the collision described in its docstring: two distinct units
# both resolving to the same claude_id.

CDXML_DUP_BOGUS = """<?xml version="1.0" ?>
<CDXML>
 <page id="1">
  <group id="90" BoundingBox="1000.0 1000.0 1001.0 1001.0">
   <fragment id="91" BoundingBox="1000.0 1000.0 1001.0 1001.0">
    <n id="92" p="1000 1000"/>
   </fragment>
   <objecttag TagType="String" Visible="no" Name="claude_id" Value="claude-dup"/>
  </group>
 </page>
</CDXML>
"""


def test_duplicate_id_both_units_fall_back_and_keep_their_own_bounds(monkeypatch):
    unit_a = FakeUnit(
        "claude-dup",
        live_bounds={"left": 10.0, "top": 10.0, "right": 20.0, "bottom": 20.0},
        atom_count=1, bond_count=0, formula="A",
    )
    unit_b = FakeUnit(
        "claude-dup",
        live_bounds={"left": 50.0, "top": 50.0, "right": 60.0, "bottom": 60.0},
        atom_count=2, bond_count=1, formula="B",
    )
    doc = FakeDoc([unit_a, unit_b], CDXML_DUP_BOGUS)
    monkeypatch.setattr(targets, "iter_units", lambda doc, cache=None: [unit_a, unit_b])

    out = state.build_snapshot(doc)

    assert unit_a.live_bounds_reads == 4, "both same-id units must fall back, not just one"
    assert unit_b.live_bounds_reads == 4, "both same-id units must fall back, not just one"
    assert len(out) == 2
    entry_a, entry_b = out

    # Neither entry may pick up the CDXML index's (bogus) shared-id bounds,
    # and neither may pick up the OTHER unit's live bounds.
    assert entry_a["bounds"] == {"left": 10.0, "top": 10.0, "right": 20.0, "bottom": 20.0}
    assert entry_b["bounds"] == {"left": 50.0, "top": 50.0, "right": 60.0, "bottom": 60.0}
    assert entry_a["atom_count"] == 1 and entry_a["bond_count"] == 0
    assert entry_b["atom_count"] == 2 and entry_b["bond_count"] == 1
    assert entry_a["formula"] == "A"
    assert entry_b["formula"] == "B"
