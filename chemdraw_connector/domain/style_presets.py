"""Journal style presets, hand-encoded from published values (no .cds style
files ship with this ChemDraw install). All lengths in points.

These match ChemDraw's own bundled stylesheets where documented:
- "ACS 1996": the classic ACS Document 1996 stylesheet.
- "RSC": Royal Society of Chemistry guidelines.
- "Wiley": Wiley/VCH journals (Angew., Chem. Eur. J.).
- "default": ChemDraw's out-of-box New Document defaults.
"""

PRESETS = {
    "ACS 1996": {
        "BondLength": 14.4,       # 0.508 cm
        "BondSpacing": 18.0,      # percent of bond length
        "LineWidth": 0.6,
        "BoldWidth": 2.0,
        "HashSpacing": 2.5,
        "MarginWidth": 1.6,
        "CaptionSize": 10.0,
        "LabelSize": 10.0,
        "CaptionFont": "Arial",
        "LabelFont": "Arial",
    },
    "RSC": {
        "BondLength": 14.4,
        "BondSpacing": 18.0,
        "LineWidth": 0.75,
        "BoldWidth": 2.0,
        "HashSpacing": 2.5,
        "MarginWidth": 1.6,
        "CaptionSize": 9.0,
        "LabelSize": 9.0,
        "CaptionFont": "Arial",
        "LabelFont": "Arial",
    },
    "Wiley": {
        "BondLength": 17.0,       # 0.6 cm
        "BondSpacing": 18.0,
        "LineWidth": 0.75,
        "BoldWidth": 2.25,
        "HashSpacing": 2.5,
        "MarginWidth": 1.6,
        "CaptionSize": 10.0,
        "LabelSize": 10.0,
        "CaptionFont": "Arial",
        "LabelFont": "Arial",
    },
    "default": {
        "BondLength": 14.4,
        "BondSpacing": 12.0,
        "LineWidth": 0.6,
        "BoldWidth": 2.0,
        "HashSpacing": 2.7,
        "MarginWidth": 2.0,
        "CaptionSize": 10.0,
        "LabelSize": 10.0,
        "CaptionFont": "Arial",
        "LabelFont": "Arial",
    },
}


def get_preset(name):
    try:
        return dict(PRESETS[name])
    except KeyError:
        raise ValueError(
            f"Unknown style preset {name!r}; available: {sorted(PRESETS)}"
        ) from None
