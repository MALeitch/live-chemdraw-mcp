"""Functional-group detection for shorthand contraction.

The SHORTHANDS table maps drawn substructures (SMARTS) to the label chemists
write for them (Ph, Boc, TBS...). Matching runs on an RDKit mol built from
the CDXML graph (domain/cdxml_graph.py); contracted nicknames already on the
canvas are dummy vertices, so SMARTS never match into them.

Safety rules enforced on every match, regardless of SMARTS quality:
  * the contracted atom set must have exactly ONE bond to the rest of the
    molecule (a shorthand label replaces a monovalent substituent);
  * the set must not be the whole molecule;
  * sets never overlap — bigger groups win (TBS beats TMS beats tBu).

Zero COM imports; pure RDKit.
"""
from dataclasses import dataclass, field

from rdkit import Chem

_BOND_TYPES = {
    1: Chem.BondType.SINGLE,
    2: Chem.BondType.DOUBLE,
    3: Chem.BondType.TRIPLE,
    4: Chem.BondType.QUADRUPLE,
    15: Chem.BondType.AROMATIC,  # drawn "1.5"
}


@dataclass(frozen=True)
class Shorthand:
    label: str            # what the contracted node will display
    smarts: str           # the drawn substructure, possibly with context atoms
    description: str
    contract: tuple = ()  # SMARTS atom indices to contract; empty = all
    auto: bool = False    # part of the conservative "auto" set?
    aliases: tuple = ()


# NOTE for maintainers: SMARTS here describe the group as DRAWN (kekulized).
# Rings drawn with an aromatic circle export all-single bond orders and will
# simply never match — that is the safe failure mode. H-counts ([CH2], [cH])
# pin substitution patterns; the boundary==1 rule above is the final gate.
SHORTHANDS = [
    # ---- silyl ----
    Shorthand("TMS", "[Si]([CH3])([CH3])[CH3]",
              "trimethylsilyl", auto=True),
    Shorthand("TES", "[Si]([CH2][CH3])([CH2][CH3])[CH2][CH3]",
              "triethylsilyl", auto=True),
    Shorthand("TBS", "[Si]([CH3])([CH3])[CX4]([CH3])([CH3])[CH3]",
              "tert-butyldimethylsilyl", auto=True, aliases=("TBDMS",)),
    Shorthand("TIPS", "[Si]([CH]([CH3])[CH3])([CH]([CH3])[CH3])[CH]([CH3])[CH3]",
              "triisopropylsilyl", auto=True),
    Shorthand("TBDPS",
              "[Si](c1[cH][cH][cH][cH][cH]1)(c1[cH][cH][cH][cH][cH]1)"
              "[CX4]([CH3])([CH3])[CH3]",
              "tert-butyldiphenylsilyl", auto=True),
    # ---- carbamate / acyl protecting groups ----
    Shorthand("Boc", "[CX3](=O)[OX2][CX4]([CH3])([CH3])[CH3]",
              "tert-butoxycarbonyl", auto=True),
    Shorthand("Cbz", "[CX3](=O)[OX2][CH2]c1[cH][cH][cH][cH][cH]1",
              "benzyloxycarbonyl", auto=True),
    Shorthand("Fmoc",
              "[CX3](=O)[OX2][CH2][CH]1c2[cH][cH][cH][cH]c2-c2[cH][cH][cH][cH]c21",
              "fluorenylmethyloxycarbonyl", auto=True),
    Shorthand("Alloc", "[CX3](=O)[OX2][CH2][CH]=[CH2]",
              "allyloxycarbonyl"),
    Shorthand("Ac", "[CX3](=O)[CH3]", "acetyl", auto=True),
    Shorthand("Piv", "[CX3](=O)[CX4]([CH3])([CH3])[CH3]",
              "pivaloyl", auto=True),
    Shorthand("Bz", "[CX3](=O)c1[cH][cH][cH][cH][cH]1",
              "benzoyl", auto=True),
    # ---- sulfonyl ----
    Shorthand("Ms", "[SX4](=O)(=O)[CH3]", "methanesulfonyl (mesyl)",
              auto=True, aliases=("mesyl",)),
    Shorthand("Ts", "[SX4](=O)(=O)c1[cH][cH]c([CH3])[cH][cH]1",
              "p-toluenesulfonyl (tosyl)", auto=True, aliases=("tosyl",)),
    Shorthand("Tf", "[SX4](=O)(=O)[CX4](F)(F)F",
              "trifluoromethanesulfonyl (triflyl)", auto=True),
    Shorthand("Ns", "[SX4](=O)(=O)c1[cH][cH]c([NX3+](=[OX1])[OX1-])[cH][cH]1",
              "4-nitrobenzenesulfonyl (nosyl)", aliases=("nosyl",)),
    # ---- aryl / arylalkyl ----
    Shorthand("Ph", "c1ccccc1", "phenyl (monosubstituted benzene ring)",
              auto=True),
    # Bn/PMB are shorthand on heteroatoms (OBn, NBn...); on carbon the ring
    # is just a phenyl, so a context heteroatom (not contracted) is required.
    Shorthand("Bn", "[#7,#8,#16][CH2]c1[cH][cH][cH][cH][cH]1", "benzyl",
              contract=(1, 2, 3, 4, 5, 6, 7), auto=True),
    Shorthand("PMB", "[#7,#8,#16][CH2]c1[cH][cH]c([OX2][CH3])[cH][cH]1",
              "para-methoxybenzyl",
              contract=(1, 2, 3, 4, 5, 6, 7, 8, 9), auto=True),
    Shorthand("PMP", "[cH0]1[cH][cH]c([OX2][CH3])[cH][cH]1",
              "para-methoxyphenyl"),
    Shorthand("p-Tol", "[cH0]1[cH][cH]c([CH3])[cH][cH]1",
              "para-tolyl", aliases=("tol", "tolyl")),
    Shorthand("Mes", "[cH0]1c([CH3])[cH]c([CH3])[cH]c1[CH3]",
              "mesityl (2,4,6-trimethylphenyl)"),
    Shorthand("Trt",
              "[CX4](c1[cH][cH][cH][cH][cH]1)(c1[cH][cH][cH][cH][cH]1)"
              "c1[cH][cH][cH][cH][cH]1",
              "triphenylmethyl (trityl)", auto=True, aliases=("trityl",)),
    # ---- O,O-acetal-type protecting groups (context O excluded from set) ----
    Shorthand("MOM", "[OX2][CH2][OX2][CH3]", "methoxymethyl (on O)",
              contract=(1, 2, 3)),
    Shorthand("MEM", "[OX2][CH2][OX2][CH2][CH2][OX2][CH3]",
              "2-methoxyethoxymethyl (on O)", contract=(1, 2, 3, 4, 5, 6)),
    Shorthand("SEM", "[OX2][CH2][OX2][CH2][CH2][Si]([CH3])([CH3])[CH3]",
              "2-(trimethylsilyl)ethoxymethyl (on O)",
              contract=(1, 2, 3, 4, 5, 6, 7, 8)),
    Shorthand("THP", "[OX2][CH]1[OX2][CH2][CH2][CH2][CH2]1",
              "tetrahydropyran-2-yl (on O)", contract=(1, 2, 3, 4, 5, 6)),
    # ---- boron ----
    Shorthand("Bpin", "[BX3]1[OX2][CX4]([CH3])([CH3])[CX4]([CH3])([CH3])[OX2]1",
              "pinacol boronate", auto=True, aliases=("pin",)),
    # ---- small carbon groups (opt-in: too common to contract automatically) ----
    Shorthand("Me", "[CH3]", "methyl"),
    Shorthand("Et", "[CH2][CH3]", "ethyl"),
    Shorthand("nPr", "[CH2][CH2][CH3]", "n-propyl"),
    Shorthand("iPr", "[CH]([CH3])[CH3]", "isopropyl"),
    Shorthand("nBu", "[CH2][CH2][CH2][CH3]", "n-butyl"),
    Shorthand("tBu", "[CX4]([CH3])([CH3])[CH3]", "tert-butyl"),
    Shorthand("Cy", "[CH]1[CH2][CH2][CH2][CH2][CH2]1", "cyclohexyl"),
    Shorthand("Allyl", "[CH2][CH]=[CH2]", "allyl"),
    Shorthand("Vinyl", "[CH]=[CH2]", "vinyl"),
    Shorthand("CF3", "[CX4](F)(F)F", "trifluoromethyl", auto=True),
    Shorthand("CCl3", "[CX4](Cl)(Cl)Cl", "trichloromethyl"),
    # ---- heteroatom-containing formula labels (opt-in) ----
    Shorthand("OMe", "[OX2][CH3]", "methoxy"),
    Shorthand("OEt", "[OX2][CH2][CH3]", "ethoxy"),
    Shorthand("SMe", "[SX2][CH3]", "methylthio"),
    Shorthand("NMe2", "[NX3]([CH3])[CH3]", "dimethylamino"),
    Shorthand("NO2", "[NX3+](=[OX1])[OX1-]", "nitro (drawn charge-separated)"),
    Shorthand("CN", "[CX2]#[NX1]", "nitrile"),
    Shorthand("CO2Me", "[CX3](=O)[OX2][CH3]", "methyl ester"),
    Shorthand("CO2Et", "[CX3](=O)[OX2][CH2][CH3]", "ethyl ester"),
]

_BY_KEY = {}
for _sh in SHORTHANDS:
    _BY_KEY[_sh.label.lower()] = _sh
    for _al in _sh.aliases:
        _BY_KEY[_al.lower()] = _sh


def available_groups():
    return [
        {"label": s.label, "description": s.description, "auto": s.auto}
        for s in SHORTHANDS
    ]


def resolve_groups(groups):
    """groups: 'auto' | label | comma-separated labels | list of labels."""
    if isinstance(groups, str):
        if groups.strip().lower() in ("auto", "", "all"):
            return [s for s in SHORTHANDS if s.auto]
        groups = [g for g in groups.split(",") if g.strip()]
    out, unknown = [], []
    for g in groups:
        sh = _BY_KEY.get(str(g).strip().lower())
        (out.append(sh) if sh else unknown.append(g))
    if unknown:
        known = ", ".join(s.label for s in SHORTHANDS)
        raise ValueError(
            f"Unknown shorthand group(s) {unknown!r}. Known groups: {known}")
    if not out:
        raise ValueError("No shorthand groups given")
    # dedupe, keep order
    seen = set()
    return [s for s in out if not (s.label in seen or seen.add(s.label))]


def build_mol(graph):
    """RDKit mol from a cdxml_graph.parse() result.

    Returns (mol, id_by_idx) where id_by_idx maps RDKit atom index ->
    CDXML/COM atom id. Nickname/fragment nodes become dummy atoms ([*]),
    invisible to element SMARTS but present for connectivity/H-counts.
    """
    rw = Chem.RWMol()
    id_by_idx = {}
    idx_by_id = {}
    for node in graph["nodes"]:
        if node["is_real_atom"]:
            atom = Chem.Atom(node["element"])
            atom.SetFormalCharge(node["charge"])
        else:
            atom = Chem.Atom(0)
            atom.SetNoImplicit(True)
        idx = rw.AddAtom(atom)
        id_by_idx[idx] = node["id"]
        idx_by_id[node["id"]] = idx
    for bond in graph["bonds"]:
        bt = _BOND_TYPES.get(bond["order"], Chem.BondType.SINGLE)
        rw.AddBond(idx_by_id[bond["begin"]], idx_by_id[bond["end"]], bt)
    mol = rw.GetMol()
    try:
        Chem.SanitizeMol(mol)
    except Exception as exc:
        raise ValueError(
            f"Structure could not be chemically interpreted for substructure "
            f"matching: {exc}") from exc
    return mol, id_by_idx


def find_contractions(mol, id_by_idx, shorthands):
    """All non-overlapping contractible groups, biggest first.

    Returns [{"label", "atom_ids"}] where atom_ids are CDXML/COM ids of the
    atoms to collapse into the label.
    """
    ranked = []
    for sh in shorthands:
        patt = Chem.MolFromSmarts(sh.smarts)
        if patt is None:  # a bad table entry must not take down the tool
            continue
        size = len(sh.contract) if sh.contract else patt.GetNumAtoms()
        ranked.append((size, sh, patt))
    ranked.sort(key=lambda t: t[0], reverse=True)

    # Kekulized copy: rings drawn/exported as aromatic (order "1.5", e.g.
    # after a SMILES insert draws an aromatic circle) lose their circle on
    # contraction, so the caller must first write these explicit single/
    # double orders onto the matched bonds or the nested fragment degrades
    # to the all-single-bond skeleton.
    kek = Chem.Mol(mol)
    Chem.Kekulize(kek, clearAromaticFlags=True)

    claimed = set()
    found = []
    for _, sh, patt in ranked:
        for match in mol.GetSubstructMatches(patt, uniquify=True):
            idxs = ([match[i] for i in sh.contract] if sh.contract
                    else list(match))
            cset = set(idxs)
            if cset & claimed:
                continue
            if len(cset) >= mol.GetNumAtoms():
                continue  # refuse to swallow the whole molecule
            if _boundary_bonds(mol, cset) != 1:
                continue  # shorthand labels replace monovalent substituents
            claimed |= cset
            bond_orders = []
            for bond in kek.GetBonds():
                i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
                if i in cset and j in cset:
                    bond_orders.append((id_by_idx[i], id_by_idx[j],
                                        int(bond.GetBondTypeAsDouble())))
            found.append({
                "label": sh.label,
                "atom_ids": sorted(id_by_idx[i] for i in cset),
                "bond_orders": bond_orders,
            })
    return found


def _boundary_bonds(mol, atom_set):
    n = 0
    for bond in mol.GetBonds():
        if (bond.GetBeginAtomIdx() in atom_set) != (bond.GetEndAtomIdx() in atom_set):
            n += 1
    return n
