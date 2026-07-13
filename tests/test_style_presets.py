import pytest

from chemdraw_connector.domain.style_presets import PRESETS, get_preset


def test_all_presets_have_core_keys():
    for name, preset in PRESETS.items():
        for key in ("BondLength", "LineWidth", "CaptionSize", "CaptionFont"):
            assert key in preset, f"{name} missing {key}"


def test_acs_bond_length():
    assert get_preset("ACS 1996")["BondLength"] == 14.4


def test_get_preset_copies():
    a = get_preset("ACS 1996")
    a["BondLength"] = 999
    assert get_preset("ACS 1996")["BondLength"] == 14.4


def test_unknown_preset():
    with pytest.raises(ValueError, match="Unknown style preset"):
        get_preset("Nature")
