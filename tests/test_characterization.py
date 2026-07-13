import pytest

from chemdraw_connector.domain.characterization import hrms_line


def test_mh_plus_aspirin():
    line = hrms_line("C9H8O4", 180.0423, "[M+H]+")
    assert line == (
        "HRMS (ESI) m/z calc'd for C9H8O4 [M+H]+: 181.0496, found: ____."
    )


def test_m_minus_h():
    line = hrms_line("C9H8O4", 180.0423, "[M-H]-")
    assert "179.0350" in line


def test_found_value():
    line = hrms_line("C9H8O4", 180.0423, "[M+H]+", found=181.0492)
    assert line.endswith("found: 181.0492.")


def test_unknown_adduct():
    with pytest.raises(ValueError, match="Unknown ion mode"):
        hrms_line("CH4", 16.0313, "[M+Cs]+")
