import pytest

from chemdraw_connector.domain.enumeration import (
    enumerate_derivatives, fuse, validate_smiles,
)
from rdkit import Chem


def test_validate_smiles_ok():
    # Kekulized on purpose (explicit double bonds) so ChemDraw never draws
    # an aromatic-circle ring for anything we insert — see validate_smiles.
    assert validate_smiles("c1ccccc1") == "C1=CC=CC=C1"


def test_validate_smiles_bad():
    with pytest.raises(ValueError, match="Invalid SMILES"):
        validate_smiles("c1ccccc")  # unclosed ring


def test_fuse_phenyl_onto_scaffold():
    # scaffold: para-substituted toluene with [*] at the R position
    mol = fuse("Cc1ccc(cc1)[*]", "[*]c1ccncc1")
    smiles = Chem.MolToSmiles(mol)
    assert Chem.MolFromSmiles(smiles) is not None
    assert mol.GetNumAtoms() == 13  # 7 (tolyl) + 6 (pyridyl)


def test_fuse_substituent_without_star_uses_first_atom():
    mol = fuse("C[*]", "OC")  # attach at O -> ethanol-like C-O-C? No: C-OC = methoxymethane
    assert Chem.MolToSmiles(mol) == "COC"


def test_enumerate_collects_failures():
    rows, failures = enumerate_derivatives(
        "Cc1ccc(cc1)[*]",
        ["[*]c1ccncc1", "not-a-smiles"],
        ["mw", "formula"],
    )
    assert len(rows) == 1
    assert rows[0]["formula"]
    assert rows[0]["mw"] > 0
    assert len(failures) == 1
    assert failures[0]["substituent"] == "not-a-smiles"


def test_enumerate_properties():
    rows, failures = enumerate_derivatives(
        "c1ccccc1[*]", ["[*]O"],
        ["mw", "exact_mass", "formula", "logp", "tpsa", "hbd", "hba",
         "rotatable_bonds", "inchikey"],
    )
    assert not failures
    row = rows[0]  # phenol
    assert row["formula"] == "C6H6O"
    assert abs(row["mw"] - 94.11) < 0.1
    assert row["hbd"] == 1
    assert row["inchikey"].startswith("ISWSIDIOOBJBQZ")  # phenol InChIKey


def test_scaffold_without_star_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        fuse("c1ccccc1", "[*]O")
