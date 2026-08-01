"""Semantic translation between raw COM values and readable vocabulary.

Every value that reaches Claude goes through here — element symbols instead
of atomic numbers, bond-order names instead of CDX integer flags, and so on.
Unknown raw values pass through as strings rather than raising, so a future
ChemDraw enum addition degrades to "unknown(<n>)" instead of an error.
"""

ELEMENTS = [
    "?", "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg",
    "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn",
    "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb",
    "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In",
    "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm",
    "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta",
    "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At",
    "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk",
    "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt",
    "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
]
SYMBOL_TO_NUMBER = {sym: i for i, sym in enumerate(ELEMENTS) if i}

# CDX bond order flags (ChemDraw's native encoding).
BOND_ORDER_NAMES = {
    1: "single", 2: "double", 4: "triple", 8: "quadruple",
    16: "quintuple", 32: "sextuple", 64: "half",
    128: "aromatic", 256: "dative", 512: "ionic", 1024: "hydrogen",
}
BOND_ORDER_VALUES = {v: k for k, v in BOND_ORDER_NAMES.items()}

# CDX bond display (kCDBondDisplay).
BOND_DISPLAY_NAMES = {
    0: "plain", 1: "dash", 2: "hash", 3: "wedged-hash-begin",
    4: "wedged-hash-end", 5: "bold", 6: "wedge-begin", 7: "wedge-end",
    8: "wavy", 9: "hollow-wedge-begin", 10: "hollow-wedge-end",
    11: "wedged-hash-begin-no-taper", 12: "wedged-hash-end-no-taper",
    13: "dotted", 14: "unknown",
}
# The vocabulary tools accept for setting stereo bond display.
BOND_DISPLAY_VALUES = {
    "plain": 0,
    "wedge": 6,       # wedge pointing away from Atom1 (standard "up")
    "hash": 3,        # hashed wedge from Atom1 (standard "down")
    "wavy": 8,
    "bold": 5,
    "dash": 1,
}

# CDX CIP stereo descriptors as ChemDraw derives them from drawn geometry.
ATOM_CIP_NAMES = {0: None, 1: None, 2: "R", 3: "S", 4: "r", 5: "s",
                  6: "unspecified"}
BOND_CIP_NAMES = {0: None, 1: None, 2: "E", 3: "Z"}

# MIME data-type strings, keyed by the format names tools expose.
FORMAT_MIME = {
    "smiles": "chemical/x-daylight-smiles",
    "molfile": "chemical/x-mdl-molfile",
    "molfile-v3000": "chemical/x-mdl-molfile-v3000",
    "inchi": "chemical/x-inchi",
    "inchikey": "chemical/x-inchikey",
    "name": "chemical/x-name",
    "htmlname": "chemical/x-htmlname",
    "cdxml": "text/xml",
    "cdx": "chemical/x-cdx",
    "cml": "chemical/x-cml",
    "helm": "chemical/x-helm",
    "png": "image/png",
    "svg": "image/svg+xml",
    "emf": "image/x-emf",
}

# CDX node types (kCDNodeType...). Element(1)/Unspecified(0) are plain
# drawn atoms; everything else (Fragment=5, the type ContractObjectsToLabel
# produces; Nickname=4, ChemDraw's own dictionary-typed nicknames; Formula=6,
# GenericNickname=7, ElementList=2/3...) is some collapsed/label
# representation. Probed live: an ORDINARY heteroatom like N or O also has
# non-empty LabelText (that's just how ChemDraw draws its symbol), so
# LabelText alone can't tell "contracted shorthand label" apart from
# "this is how nitrogen is drawn" — NodeType is the property that can.
NODE_TYPE_ORDINARY_ATOM = {0, 1}

POINTS_PER_INCH = 72.0


def element_symbol(number):
    try:
        n = int(number)
    except (ValueError, TypeError):
        return f"unknown({number})"
    # Explicit bounds check, not a bare ELEMENTS[n] try/except IndexError:
    # Python list indexing wraps negative indices instead of raising, so
    # e.g. -1 would silently return ELEMENTS[-1] ("Og") rather than
    # degrading to "unknown(-1)" as this function's contract promises.
    if not 0 <= n < len(ELEMENTS):
        return f"unknown({number})"
    return ELEMENTS[n]


def element_number(symbol):
    try:
        return SYMBOL_TO_NUMBER[symbol.capitalize() if len(symbol) > 1 else symbol.upper()]
    except (KeyError, AttributeError):
        raise ValueError(f"Unknown element symbol: {symbol!r}") from None


def bond_order_name(value):
    return BOND_ORDER_NAMES.get(int(value), f"unknown({value})")


def bond_order_value(name):
    try:
        return BOND_ORDER_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown bond order {name!r}; expected one of "
            f"{sorted(BOND_ORDER_VALUES)}"
        ) from None


def bond_display_name(value):
    return BOND_DISPLAY_NAMES.get(int(value), f"unknown({value})")


def bond_display_value(name):
    try:
        return BOND_DISPLAY_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown bond display {name!r}; expected one of "
            f"{sorted(BOND_DISPLAY_VALUES)}"
        ) from None


def rgb_hex_to_colorref(hex_str):
    """'#RRGGBB' -> Windows COLORREF int (0x00BBGGRR), the format
    IChemDrawObject.Color actually takes -- confirmed live (2026-07-31):
    setting an Atom/Bond's .Color to 0x0000FF renders red, 0x00FF0000
    renders blue, i.e. byte order is B,G,R, not R,G,B. Every caller of
    this connector gives colors as ordinary '#RRGGBB' hex; the BGR
    reordering happens here, once, so nobody else has to get it backwards."""
    s = hex_str.lstrip("#")
    if len(s) != 6:
        raise ValueError(
            f"Expected a 6-digit hex color like '#FF8000', got {hex_str!r}")
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        raise ValueError(
            f"Expected a 6-digit hex color like '#FF8000', got {hex_str!r}"
        ) from None
    return (b << 16) | (g << 8) | r


def colorref_value_to_rgb_hex(value):
    """Inverse of rgb_hex_to_colorref, for reporting a post-write Color
    readback back to a caller as ordinary '#RRGGBB' instead of a raw BGR
    int. value=0 (ChemDraw's default/unset) reports as '#000000' (black),
    which is correct -- unset Color renders as plain black, same as an
    explicit black."""
    value = int(value)
    r = value & 0xFF
    g = (value >> 8) & 0xFF
    b = (value >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def mime_for(fmt):
    try:
        return FORMAT_MIME[fmt]
    except KeyError:
        raise ValueError(
            f"Unknown format {fmt!r}; expected one of {sorted(FORMAT_MIME)}"
        ) from None


# CDArrowHeadType (kCDArrowHeadType...) -- confirmed LIVE 2026-07-21 that
# this, not CDArrowType, is what IChemDrawArrow.ArrowHeadType actually takes:
# it's the arrowhead's FILL style (solid triangle / hollow outline / bare
# angle mark), not a head-count or mechanism-class bitmask as the raw
# CDArrowType enum's names (NoHead/HalfHead/FullHead/Resonance/Equilibrium/
# Hollow/RetroSynthetic) would suggest if applied here — that assumption was
# tested live and found wrong before any bridge code was written around it.
ARROW_HEAD_TYPE_NAMES = {0: "unspecified", 1: "solid", 2: "hollow", 3: "angle"}
ARROW_HEAD_TYPE_VALUES = {v: k for k, v in ARROW_HEAD_TYPE_NAMES.items()}

# CDArrowHeadPositionType (kCDArrowHeadPosition...) -- confirmed LIVE
# 2026-07-21 as the real per-end head control on IChemDrawArrow
# (ArrowHeadPositionStart/ArrowHeadPositionTail): "none" (no head at that
# end), "full" (both barbs, the normal filled/hollow triangle), and
# "half_left"/"half_right" (single-barb -- verified live via a large
# HeadSize/HeadWidth + 600 DPI screenshot to render a genuine asymmetric
# "fishhook" shape, the classic single-electron mechanism-arrow head).
ARROW_HEAD_POSITION_NAMES = {0: "unspecified", 1: "none", 2: "full",
                              3: "half_left", 4: "half_right"}
ARROW_HEAD_POSITION_VALUES = {v: k for k, v in ARROW_HEAD_POSITION_NAMES.items()}

# CDNoGoType (kCDNoGoType...) -- a crossed-out/hashed "this reaction pathway
# does not occur" arrow marker, found live on IChemDrawArrow alongside the
# head-position properties above (not in the original session's typelib
# scan target list).
NO_GO_TYPE_NAMES = {0: "unspecified", 1: "none", 2: "cross", 3: "hash"}
NO_GO_TYPE_VALUES = {v: k for k, v in NO_GO_TYPE_NAMES.items()}

# CDSymbolType (kCDSymbolType...). Types 10-12 (racemic/absolute/relative)
# render as clear boxed stereo-descriptor labels ("Rac"/"Abs"/"Rel",
# confirmed live via screenshot). Types 0-9 render at a tiny, apparently
# unscalable default size (confirmed live: bounds as small as ~0.13pt for
# the electron-dot type, with no settable size property found) -- exposed
# here regardless since the enum itself is simple vocabulary, but
# chemdraw_make_symbol's docstring should steer callers toward 10-12 until
# a sizing mechanism for the rest is found.
SYMBOL_TYPE_NAMES = {0: "lone_pair", 1: "electron", 2: "radical_cation",
                      3: "radical_anion", 4: "circle_plus", 5: "circle_minus",
                      6: "dagger", 7: "double_dagger", 8: "plus", 9: "minus",
                      10: "racemic", 11: "absolute", 12: "relative"}
SYMBOL_TYPE_VALUES = {v: k for k, v in SYMBOL_TYPE_NAMES.items()}

# CDEnhancedStereoType (kCDEnhancedStereo...) -- the "and1"/"or1" relative-
# stereo grouping notation. Found live directly on IChemDrawAtom
# (EnhancedStereoType + EnhancedStereoGroupNumber), confirmed settable.
ENHANCED_STEREO_TYPE_NAMES = {0: "unspecified", 1: "none", 2: "absolute",
                               3: "or", 4: "and"}
ENHANCED_STEREO_TYPE_VALUES = {v: k for k, v in ENHANCED_STEREO_TYPE_NAMES.items()}

# CDBracketType (kCDBracketType...) -- the bracket glyph's line style.
# Confirmed live via screenshot: square is a straight line with right-angle
# hook end-caps, curly renders as an actual curly-brace curve with a small
# center bulge, round renders as a big parenthesis-like arc.
BRACKET_TYPE_NAMES = {0: "square", 1: "curly", 2: "round"}
BRACKET_TYPE_VALUES = {v: k for k, v in BRACKET_TYPE_NAMES.items()}

# CDBracketUsage (kCDBracketUsage...) -- what a bracket represents
# semantically. Confirmed live: this also drives an automatic abbreviation
# label rendered next to the bracket (sru -> "n", monomer -> "mon",
# crosslink -> "xl", unspecified -> no label at all) -- ChemDraw generates
# this text itself; see make_bracket's docstring for why it can't be
# overridden via SRULabel/RepeatCount.
BRACKET_USAGE_NAMES = {
    0: "unspecified", 1: "unused1", 2: "unused2", 3: "sru", 4: "monomer",
    5: "mer", 6: "copolymer", 7: "copolymer_alternating",
    8: "copolymer_random", 9: "copolymer_block", 10: "crosslink",
    11: "graft", 12: "modification", 13: "component",
    14: "mixture_unordered", 15: "mixture_ordered", 16: "multiple_group",
    17: "generic", 18: "anypolymer",
}
BRACKET_USAGE_VALUES = {v: k for k, v in BRACKET_USAGE_NAMES.items()}

# CDPolymerRepeatPattern (kCDPolymerRepeatPattern...) -- confirmed settable
# live (get/put round-trip on a fresh bracket), but this session did not
# confirm a distinguishable visual effect for any of its 3 values -- expose
# it since it's harmless to set, but don't assume it does anything visible
# yet (see make_bracket's docstring).
POLYMER_REPEAT_PATTERN_NAMES = {0: "head_to_tail", 1: "head_to_head",
                                 2: "either_unknown"}
POLYMER_REPEAT_PATTERN_VALUES = {v: k for k, v in POLYMER_REPEAT_PATTERN_NAMES.items()}


def bracket_type_name(value):
    return BRACKET_TYPE_NAMES.get(int(value), f"unknown({value})")


def bracket_type_value(name):
    try:
        return BRACKET_TYPE_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown bracket type {name!r}; expected one of "
            f"{sorted(BRACKET_TYPE_VALUES)}"
        ) from None


def bracket_usage_name(value):
    return BRACKET_USAGE_NAMES.get(int(value), f"unknown({value})")


def bracket_usage_value(name):
    try:
        return BRACKET_USAGE_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown bracket usage {name!r}; expected one of "
            f"{sorted(BRACKET_USAGE_VALUES)}"
        ) from None


def polymer_repeat_pattern_name(value):
    return POLYMER_REPEAT_PATTERN_NAMES.get(int(value), f"unknown({value})")


def polymer_repeat_pattern_value(name):
    try:
        return POLYMER_REPEAT_PATTERN_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown polymer repeat pattern {name!r}; expected one of "
            f"{sorted(POLYMER_REPEAT_PATTERN_VALUES)}"
        ) from None


def arrow_head_type_name(value):
    return ARROW_HEAD_TYPE_NAMES.get(int(value), f"unknown({value})")


def arrow_head_type_value(name):
    try:
        return ARROW_HEAD_TYPE_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown arrow head type {name!r}; expected one of "
            f"{sorted(ARROW_HEAD_TYPE_VALUES)}"
        ) from None


def arrow_head_position_name(value):
    return ARROW_HEAD_POSITION_NAMES.get(int(value), f"unknown({value})")


def arrow_head_position_value(name):
    try:
        return ARROW_HEAD_POSITION_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown arrow head position {name!r}; expected one of "
            f"{sorted(ARROW_HEAD_POSITION_VALUES)}"
        ) from None


def no_go_type_name(value):
    return NO_GO_TYPE_NAMES.get(int(value), f"unknown({value})")


def no_go_type_value(name):
    try:
        return NO_GO_TYPE_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown no-go type {name!r}; expected one of "
            f"{sorted(NO_GO_TYPE_VALUES)}"
        ) from None


def symbol_type_name(value):
    return SYMBOL_TYPE_NAMES.get(int(value), f"unknown({value})")


def symbol_type_value(name):
    try:
        return SYMBOL_TYPE_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown symbol type {name!r}; expected one of "
            f"{sorted(SYMBOL_TYPE_VALUES)}"
        ) from None


def enhanced_stereo_type_name(value):
    return ENHANCED_STEREO_TYPE_NAMES.get(int(value), f"unknown({value})")


def enhanced_stereo_type_value(name):
    try:
        return ENHANCED_STEREO_TYPE_VALUES[name]
    except KeyError:
        raise ValueError(
            f"Unknown enhanced stereo type {name!r}; expected one of "
            f"{sorted(ENHANCED_STEREO_TYPE_VALUES)}"
        ) from None
