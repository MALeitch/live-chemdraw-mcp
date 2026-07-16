"""RDKit-based derivative enumeration and property calculation.

The scaffold carries one attachment point written as a dummy atom ([*] in
SMILES). Each substituent either carries its own [*] marking the bond atom,
or — if it has none — one is attached to its first atom as a documented
convention. Fusion happens on validated molecular graphs (RDKit molzip), not
by string editing, and every product must survive sanitization.
"""
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors, Crippen, RemoveHs
from rdkit.Chem.inchi import MolToInchiKey

PROPERTY_FUNCS = {
    "smiles": lambda m: Chem.MolToSmiles(m),
    "formula": rdMolDescriptors.CalcMolFormula,
    "mw": Descriptors.MolWt,
    "exact_mass": Descriptors.ExactMolWt,
    "logp": Crippen.MolLogP,
    "tpsa": rdMolDescriptors.CalcTPSA,
    "hbd": rdMolDescriptors.CalcNumHBD,
    "hba": rdMolDescriptors.CalcNumHBA,
    "rotatable_bonds": rdMolDescriptors.CalcNumRotatableBonds,
    "inchikey": MolToInchiKey,
}


def validate_smiles(smiles, what="SMILES"):
    """Parse + sanitize; returns a Kekulized canonical SMILES (explicit
    alternating single/double bonds, no lowercase aromatic atoms) or raises
    ValueError.

    Kekulized on purpose, not just canonicalized: ChemDraw draws lowercase
    aromatic SMILES as a delocalized ring circle, which stores every ring
    bond as order-1 plus a separate decorative circle graphic (confirmed in
    CDXML export). Any later structural op that reads bond orders — Clean Up
    Structure above all — sees only single bonds and can silently corrupt
    fused/heteroatom-rich rings drawn that way (confirmed live: produced
    radical-valence nitrogens on a polyheteroaromatic scope table). Handing
    ChemDraw real Kekule bonds up front avoids the circle, and the corruption
    risk, at the source instead of repairing it after the fact.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid {what}: {smiles!r} failed RDKit parsing/sanitization")
    Chem.Kekulize(mol, clearAromaticFlags=True)
    return Chem.MolToSmiles(mol, kekuleSmiles=True)


def validate_molblock(molblock):
    """Parse + sanitize; returns a Kekulized molblock (see validate_smiles
    for why) or raises ValueError."""
    mol = Chem.MolFromMolBlock(molblock)
    if mol is None:
        raise ValueError("Invalid molfile: failed RDKit parsing/sanitization")
    Chem.Kekulize(mol, clearAromaticFlags=True)
    return Chem.MolToMolBlock(mol, kekulize=True)


def _prep_with_map(smiles, map_num, what):
    """Parse; ensure exactly one dummy atom; stamp it with an atom map."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid {what}: {smiles!r}")
    dummies = [a for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    if len(dummies) == 0 and what == "substituent":
        # Convention: attach at the first atom.
        mol = Chem.MolFromSmiles(f"[*]{smiles}")
        if mol is None:
            raise ValueError(
                f"Substituent {smiles!r} has no [*] and auto-attachment failed"
            )
        dummies = [a for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    if len(dummies) != 1:
        raise ValueError(
            f"{what} {smiles!r} must contain exactly one [*] attachment point "
            f"(found {len(dummies)})"
        )
    dummies[0].SetAtomMapNum(map_num)
    return mol


def fuse(scaffold_smiles, substituent_smiles):
    """Join scaffold and substituent at their [*] atoms. Returns Mol."""
    scaffold = _prep_with_map(scaffold_smiles, 1, "scaffold")
    sub = _prep_with_map(substituent_smiles, 1, "substituent")
    combined = Chem.CombineMols(scaffold, sub)
    try:
        zipped = Chem.molzip(combined)
    except Exception as exc:
        raise ValueError(
            f"Could not fuse {substituent_smiles!r} onto scaffold: {exc}"
        ) from exc
    zipped = RemoveHs(zipped)
    Chem.SanitizeMol(zipped)  # raises on chemical nonsense
    return zipped


def compute_properties(mol, properties):
    row = {}
    for prop in properties:
        fn = PROPERTY_FUNCS.get(prop)
        if fn is None:
            raise ValueError(
                f"Unknown property {prop!r}; available: {sorted(PROPERTY_FUNCS)}"
            )
        value = fn(mol)
        row[prop] = round(value, 4) if isinstance(value, float) else value
    return row


def enumerate_derivatives(scaffold_smiles, substituents, properties):
    """Returns (rows, failures). Each row: substituent + smiles + properties.
    Failures are collected, not raised — one bad fragment shouldn't sink 49
    good ones."""
    props = list(dict.fromkeys(["smiles", *properties]))
    rows, failures = [], []
    for sub in substituents:
        try:
            mol = fuse(scaffold_smiles, sub)
            row = {"substituent": sub}
            row.update(compute_properties(mol, props))
            rows.append(row)
        except ValueError as exc:
            failures.append({"substituent": sub, "error": str(exc)})
    return rows, failures
