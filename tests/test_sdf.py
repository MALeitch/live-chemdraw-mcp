"""Tests for pure-Python SDF record splitting (chemdraw_import_molfile's
offline half -- no COM, no live ChemDraw needed)."""
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from chemdraw_connector.domain.sdf import split_sdf_records

_MOLFILE = """benzene
  Mrv  01010000002D

  6  6  0  0  0  0            999 V2000
    0.0000    1.4000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2124    0.7000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2124   -0.7000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000   -1.4000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.2124   -0.7000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.2124    0.7000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  2  0  0  0  0
  2  3  1  0  0  0  0
  3  4  2  0  0  0  0
  4  5  1  0  0  0  0
  5  6  2  0  0  0  0
  6  1  1  0  0  0  0
M  END"""

_ETHANOL = """ethanol
  Mrv  01010000002D

  3  2  0  0  0  0            999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2000    0.7000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    2.4000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
  2  3  1  0  0  0  0
M  END"""


def test_single_record_no_delimiter():
    assert split_sdf_records(_MOLFILE) == [_MOLFILE]


def test_multi_record_sdf_strips_data_block():
    text = (
        _MOLFILE + "\n"
        ">  <NAME>\nbenzene\n\n$$$$\n"
        + _ETHANOL + "\n"
        ">  <NAME>\nethanol\n\n$$$$\n"
    )
    records = split_sdf_records(text)
    assert len(records) == 2
    for r in records:
        assert r.endswith("M  END")
        assert "NAME" not in r
    # The real regression this guards against: the SECOND (and every
    # later) record starts right after a "$$$$" delimiter, which leaves a
    # leading blank line unless split_sdf_records strips it -- and RDKit's
    # molfile parser is strict about fixed line positions (title/program/
    # comment/counts are lines 1-4), so an off-by-one leading blank line
    # makes every record after the first fail to parse even though the
    # content itself is perfectly valid. A string-only check (endswith,
    # "NAME" not in r) does NOT catch this -- only an actual RDKit parse
    # does, which is why this test exists in addition to the ones above.
    mols = [Chem.MolFromMolBlock(r) for r in records]
    assert all(m is not None for m in mols), (
        "one or more extracted records failed to parse as valid molfiles"
    )
    assert [rdMolDescriptors.CalcMolFormula(m) for m in mols] == [
        "C6H6", "C2H6O",
    ]


def test_empty_and_whitespace_only_input():
    assert split_sdf_records("") == []
    assert split_sdf_records("   \n\n  ") == []


def test_trailing_blank_chunk_after_last_delimiter_dropped():
    text = _MOLFILE + "\n$$$$\n\n"
    assert split_sdf_records(text) == [_MOLFILE]


def test_record_missing_m_end_kept_stripped_not_dropped():
    malformed = "not a real molfile at all"
    records = split_sdf_records(malformed + "\n$$$$\n")
    assert records == [malformed]
