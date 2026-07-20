"""Substructure table + matcher tests. Graphs come from RDKit-parsed SMILES
(kekulized, like a hand-drawn canvas structure) so no ChemDraw is needed."""
import pytest
from rdkit import Chem

from chemdraw_connector.domain import substructures


def graph_from_smiles(smiles):
    """Build the same graph shape cdxml_graph.parse() produces, with node
    ids 1000+idx so tests catch any idx/id confusion."""
    mol = Chem.MolFromSmiles(smiles)
    Chem.Kekulize(mol, clearAromaticFlags=True)
    nodes = [
        {"id": 1000 + a.GetIdx(), "element": a.GetAtomicNum(),
         "charge": a.GetFormalCharge(), "node_type": "Element",
         "is_real_atom": True}
        for a in mol.GetAtoms()
    ]
    order = {Chem.BondType.SINGLE: 1, Chem.BondType.DOUBLE: 2,
             Chem.BondType.TRIPLE: 3}
    bonds = [
        {"begin": 1000 + b.GetBeginAtomIdx(), "end": 1000 + b.GetEndAtomIdx(),
         "order": order[b.GetBondType()]}
        for b in mol.GetBonds()
    ]
    return {"nodes": nodes, "bonds": bonds}


def find(smiles, groups="auto"):
    mol, id_by_idx = substructures.build_mol(graph_from_smiles(smiles))
    return substructures.find_contractions(
        mol, id_by_idx, substructures.resolve_groups(groups))


def labels(smiles, groups="auto"):
    return [m["label"] for m in find(smiles, groups)]


# ---- phenyl basics ----

def test_ethylbenzene_finds_phenyl():
    found = find("CCc1ccccc1")
    assert [m["label"] for m in found] == ["Ph"]
    assert len(found[0]["atom_ids"]) == 6


def test_benzene_alone_is_never_swallowed():
    assert labels("c1ccccc1") == []


def test_disubstituted_ring_is_not_phenyl():
    assert "Ph" not in labels("Cc1ccc(C)cc1", groups="Ph")


def test_biphenyl_contracts_both_rings():
    # both rings are monosubstituted (by each other) -> Ph-Ph is valid
    found = find("c1ccc(-c2ccccc2)cc1", groups="Ph")
    assert len(found) == 2
    assert not set(found[0]["atom_ids"]) & set(found[1]["atom_ids"])


# ---- silyl ----

def test_tbs_ether_is_tbs_not_tms_or_tbu():
    got = labels("CCO[Si](C)(C)C(C)(C)C")
    assert got == ["TBS"]


def test_tes_group():
    got = labels("CCO[Si](CC)(CC)CC")
    assert got == ["TES"]


def test_tbdps_claims_its_phenyls():
    got = labels("CCO[Si](c1ccccc1)(c1ccccc1)C(C)(C)C")
    assert got == ["TBDPS"]


# ---- protecting groups ----

def test_boc_piperidine():
    found = find("O=C(OC(C)(C)C)N1CCCCC1")
    assert [m["label"] for m in found] == ["Boc"]
    assert len(found[0]["atom_ids"]) == 7


def test_cbz_beats_bn_and_ph():
    assert labels("O=C(OCc1ccccc1)NC") == ["Cbz"]


def test_fmoc():
    assert labels("O=C(OCC1c2ccccc2-c2ccccc21)NC") == ["Fmoc"]


def test_tosyl_beats_tolyl_and_ms():
    assert labels("Cc1ccc(S(=O)(=O)NC)cc1") == ["Ts"]


def test_triflyl_beats_cf3():
    assert labels("FC(F)(F)S(=O)(=O)N(C)C") == ["Tf"]


def test_pmb_ether():
    assert labels("COc1ccc(COC2CCCCC2)cc1") == ["PMB"]


def test_bn_only_on_heteroatoms():
    # O-benzyl ether: Bn matches, context O not contracted
    found = find("C1CCCCC1OCc1ccccc1", groups="Bn")
    assert [m["label"] for m in found] == ["Bn"]
    assert len(found[0]["atom_ids"]) == 7
    # carbon-attached "benzyl" (propylbenzene) is NOT Bn; ring is Ph
    assert labels("CCCc1ccccc1") == ["Ph"]


def test_bpin():
    assert labels("CC1(C)OB(c2ccccc2)OC1(C)C") == ["Bpin", "Ph"]


# ---- context-atom groups (contract indices) ----

def test_mom_contracts_three_atoms_not_context_oxygen():
    found = find("COCOC1CCCCC1", groups="MOM")
    assert len(found) == 1
    assert len(found[0]["atom_ids"]) == 3


def test_mom_needs_oxygen_context():
    # methoxymethyl on carbon is not a MOM group
    assert labels("COCC1CCCCC1", groups="MOM") == []


def test_thp_ring():
    found = find("C1CCCCC1OC1CCCCO1", groups="THP")
    assert [m["label"] for m in found] == ["THP"]
    assert len(found[0]["atom_ids"]) == 6


# ---- opt-in behavior ----

def test_small_alkyls_not_in_auto():
    assert labels("CC(C)OC1CCCCC1") == []          # iPr stays drawn on auto
    assert labels("CC(C)OC1CCCCC1", "iPr") == ["iPr"]


def test_alias_and_case_insensitive():
    assert labels("CCO[Si](C)(C)C(C)(C)C", groups="tbdms") == ["TBS"]


def test_unknown_group_raises():
    with pytest.raises(ValueError, match="Unknown shorthand"):
        substructures.resolve_groups("NotAGroup")


# ---- structural safety rules ----

def test_matches_never_overlap():
    found = find("CCO[Si](C)(C)C(C)(C)C", groups="TBS,TMS,tBu,Me")
    claimed = []
    for m in found:
        for a in m["atom_ids"]:
            assert a not in claimed
            claimed.append(a)
    assert found[0]["label"] == "TBS"


def test_dummy_nickname_nodes_are_inert():
    graph = graph_from_smiles("CCc1ccccc1")
    # replace the terminal methyl with a fake nickname node
    graph["nodes"][0]["is_real_atom"] = False
    graph["nodes"][0]["node_type"] = "Nickname"
    mol, id_by_idx = substructures.build_mol(graph)
    found = substructures.find_contractions(
        mol, id_by_idx, substructures.resolve_groups("auto"))
    assert [m["label"] for m in found] == ["Ph"]
    dummy_id = graph["nodes"][0]["id"]
    assert all(dummy_id not in m["atom_ids"] for m in found)


def test_atom_ids_are_graph_ids_not_indices():
    found = find("CCc1ccccc1")
    assert all(a >= 1000 for a in found[0]["atom_ids"])


# ---- Kekulize guard ----

def test_find_contractions_wraps_kekulize_failure_as_valueerror(monkeypatch):
    # A real "sanitizes fine but fails a later re-Kekulize" SMILES is hard to
    # construct reliably (in this RDKit version MolSanitizeException/
    # KekulizeException already subclasses ValueError, and SanitizeMol's own
    # kekulize step tends to reject anything a later Kekulize call would
    # reject too). We still need to guarantee find_contractions never lets a
    # raw RDKit exception through — the task's own note is that RDKit's
    # Kekulize exception is *not guaranteed* to be a ValueError subclass, and
    # the bridge's `except ValueError` guard around contraction relies on
    # that. So simulate the failure via dependency injection and check the
    # wrapping behavior directly rather than the underlying exception type.
    mol, id_by_idx = substructures.build_mol(graph_from_smiles("c1ccccc1"))

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated RDKit kekulization failure")

    monkeypatch.setattr(substructures.Chem, "Kekulize", boom)
    with pytest.raises(ValueError, match="kekuliz"):
        substructures.find_contractions(
            mol, id_by_idx, substructures.resolve_groups("auto"))
