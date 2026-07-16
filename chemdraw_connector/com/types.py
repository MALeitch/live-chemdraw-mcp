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


def mime_for(fmt):
    try:
        return FORMAT_MIME[fmt]
    except KeyError:
        raise ValueError(
            f"Unknown format {fmt!r}; expected one of {sorted(FORMAT_MIME)}"
        ) from None
