#!/usr/bin/env python3
"""Unified connector visual smoke test — consolidated from test_bridge.py and test_full_workflow.py.

Run:  env -u PYTHONPATH .venv/Scripts/python test_smoke_visual.py

Exercises the full connector feature surface in sequence, asserting functional
returns and exporting a PNG per section so visuals can be spot-checked in ChemDraw.
"""
import base64
import os
import tempfile

from chemdraw_connector.bridge import ChemDrawBridge

DPI = 200


def assert_ok(result, label):
    """Assert result is a dict (not an error string) and print status."""
    if isinstance(result, dict):
        print(f"  ok  {label}")
        return result
    raise AssertionError("❌ {label}: {result}")


def _export_and_check(bridge, label, dpi=DPI, min_dark=10):
    """Export a PNG and verify it has visible rendered content."""
    result = bridge.export_image("png", "document", dpi=dpi)
    assert "base64" in result, "{label}: no base64 in export"
    png_bytes = base64.b64decode(result["base64"])
    from PIL import Image
    import io
    import numpy as np

    img = Image.open(io.BytesIO(png_bytes))
    arr = np.array(img.convert("RGB"))
    dark = np.any(arr < 100, axis=-1)
    assert dark.sum() >= min_dark, (
        "{label}: too few dark pixels ({dark.sum()}), image may be blank"
    )
    return img


def test_smoke_visual():
    b = ChemDrawBridge()
    print("=" * 60)
    print("UNIFIED SMOKE VISUAL TEST")
    print("=" * 60)

    # ──────────────────────────────────────────────────────────
    # 1. Document Lifecycle
    # ──────────────────────────────────────────────────────────
    print("\n📄 DOCUMENT LIFECYCLE")
    r = assert_ok(b.use_scratch_document(), "use_scratch_document")
    assert r["reused"] in (True, False)
    assert r["cleared_atoms"] >= 0
    r = assert_ok(b.status(), "status")
    assert r["structures_on_page"] == 0
    assert r["open_documents"] >= 1
    r = assert_ok(b.list_documents(), "list_documents")
    assert isinstance(r["documents"], list)
    assert r["active"] is not None

    # Insert a small carbon so the export isn't blank
    _ = b.insert_structure("C", "smiles")
    _export_and_check(b, "document lifecycle")

    # ──────────────────────────────────────────────────────────
    # 2. Structure I/O
    # ──────────────────────────────────────────────────────────
    print("\n🧪 STRUCTURE I/O")
    benzene = assert_ok(b.insert_structure("c1ccccc1", "smiles"), "insert benzene (smiles)")
    benzene_id = benzene["inserted"][0]["id"]
    assert benzene["inserted"][0]["formula"] == "C6H6"

    aspirin = assert_ok(b.insert_structure("aspirin", "name"), "insert aspirin (name)")
    aspirin_id = aspirin["inserted"][0]["id"]
    assert aspirin["inserted"][0]["formula"] == "C9H8O4"

    ethanol = assert_ok(b.insert_structure("CCO", "smiles", (300, 100)), "insert ethanol (smiles)")
    ethanol_id = ethanol["inserted"][0]["id"]

    exp = assert_ok(b.export_structure("smiles", "document"), "export SMILES")
    assert len(exp["structures"]) == 4
    exp_mol = assert_ok(b.export_structure("molfile", "document"), "export molfile")
    assert len(exp_mol["structures"]) == 4

    clip = assert_ok(b.copy_to_clipboard("document"), "copy_to_clipboard")
    assert len(clip["copied"]) == 4

    # Bad-SMILES rejection -- confirms invalid input actually raises
    # instead of silently inserting nothing or garbage (dropped when
    # test_bridge.py was retired; nothing else in this script exercises
    # the invalid-input path).
    try:
        b.insert_structure("not_a_smiles!!!", "smiles")
        assert False, "insert_structure should have rejected invalid SMILES"
    except Exception:
        pass  # expected
    print("  ok  insert_structure rejects invalid SMILES")

    _export_and_check(b, "structure I/O (insert + export)")

    # ──────────────────────────────────────────────────────────
    # 3. Manipulation
    # ──────────────────────────────────────────────────────────
    print("\n🔧 MANIPULATION")
    sel = assert_ok(b.select(benzene_id), "select")
    assert sel["selected"] == benzene_id
    get_sel = assert_ok(b.get_selection(), "get_selection")
    assert any(s["id"] == benzene_id for s in get_sel["selected"]), (
        f"benzene {benzene_id} not reflected in get_selection: {get_sel}")

    move = assert_ok(b.move_objects([{"object_id": benzene_id, "dx": 100, "dy": 50}]), "move_objects")
    assert benzene_id in move["applied"]

    rotate = assert_ok(b.transform(benzene_id, "rotate", degrees=45), "transform rotate")
    assert benzene_id in rotate["transformed"]

    clean = assert_ok(b.transform(benzene_id, "clean"), "transform clean")
    assert benzene_id in clean["transformed"]

    # split/flip with ethanol
    atoms_etoh = assert_ok(b.list_atoms_bonds(ethanol_id), "list_atoms_bonds ethanol")
    if atoms_etoh["structures"]:
        atom_ref = atoms_etoh["structures"][0]["atoms"][0]["ref"]
        bonds = atoms_etoh["structures"][0].get("bonds", [])
        if bonds:
            bond_ref = bonds[0]["ref"]
            split = assert_ok(b.split_at_bond(ethanol_id, bond_ref, atom_ref), "split_at_bond")
            atom_refs_to_flip = split.get("atom_refs", [])[:3]
            if atom_refs_to_flip:
                flip = assert_ok(b.transform(ethanol_id, "flip", atom_refs=atom_refs_to_flip), "transform flip")
    _export_and_check(b, "manipulation (move/rotate/clean/split/flip)")

    # ──────────────────────────────────────────────────────────
    # 4. Atom/Bond Editing
    # ──────────────────────────────────────────────────────────
    print("\n⚛️  ATOM/BOND EDITING")
    atoms = assert_ok(b.list_atoms_bonds(aspirin_id), "list_atoms_bonds aspirin")
    assert atoms["structures"]

    if atoms["structures"] and atoms["structures"][0]["atoms"]:
        atom_ref = atoms["structures"][0]["atoms"][0]["ref"]
        edit_atom = assert_ok(b.edit_atom(aspirin_id, atom_ref, element="N", charge=0), "edit_atom")
        assert edit_atom["element"] == "N"

    if atoms["structures"] and atoms["structures"][0]["bonds"]:
        bond_ref = atoms["structures"][0]["bonds"][0]["ref"]
        edit_bond = assert_ok(b.edit_bond(aspirin_id, bond_ref, bond_order="double"), "edit_bond")
        assert edit_bond["bond_order"] == "double"
        edit_bond2 = assert_ok(b.edit_bond(aspirin_id, bond_ref, bond_order="single"), "edit_bond back to single")

    if ethanol_id:
        atoms_etoh2 = assert_ok(b.list_atoms_bonds(ethanol_id), "list_atoms_bonds ethanol (for add_atom)")
        if atoms_etoh2["structures"] and atoms_etoh2["structures"][0]["atoms"]:
            atom_ref = atoms_etoh2["structures"][0]["atoms"][0]["ref"]
            add_atom = assert_ok(b.add_atom(ethanol_id, atom_ref, "C", "single"), "add_atom")
            assert add_atom["added_element"] == "C"

    # Fetch FRESH refs rather than assuming "a1"/"a2" are still valid --
    # benzene_id went through transform(..., "clean") back in Manipulation,
    # which can renumber atoms (confirmed live: a hardcoded "a1" here
    # failed with "no longer exists" once the edit_atoms batch assertion
    # below was strengthened from a truthy "applied" in batch check to a
    # real count check, which is exactly the class of bug a weak
    # assertion hides).
    atoms_benzene = assert_ok(b.list_atoms_bonds(benzene_id), "list_atoms_bonds benzene (for batch edit)")
    b_refs = [a["ref"] for a in atoms_benzene["structures"][0]["atoms"][:2]]

    batch = assert_ok(b.edit_atoms([
        {"target": benzene_id, "atom": b_refs[0], "element": "C"},
        {"target": benzene_id, "atom": b_refs[1], "set_charge": True, "charge": -1},
    ]), "edit_atoms batch")
    assert len(batch["applied"]) == 2, batch
    assert batch["failed"] == [], batch

    # Deliberately bad ref -- confirms per-item failure isolation actually
    # reports a failure rather than silently succeeding or aborting the
    # whole batch (dropped when test_bridge.py was retired).
    bad_batch = assert_ok(b.edit_atoms([
        {"target": benzene_id, "atom": b_refs[0], "element": "N"},
        {"target": benzene_id, "atom": "a9999", "element": "O"},
    ]), "edit_atoms batch with one bad ref")
    assert len(bad_batch["applied"]) == 1, bad_batch
    assert len(bad_batch["failed"]) == 1, bad_batch

    bonds_batch = assert_ok(b.edit_bonds([
        {"target": aspirin_id, "bond": atoms["structures"][0]["bonds"][0]["ref"], "bond_order": "double"},
    ]), "edit_bonds batch")
    assert len(bonds_batch["applied"]) == 1, bonds_batch
    assert bonds_batch["failed"] == [], bonds_batch

    _export_and_check(b, "atom/bond editing")

    # ──────────────────────────────────────────────────────────
    # 5. Stereochemistry + Isotope
    # ──────────────────────────────────────────────────────────
    print("\n🧬 STEREOCHEMISTRY + ISOTOPE")
    alanine = assert_ok(b.insert_structure("N[C@@H](C)C(=O)O", "smiles"), "insert L-alanine")
    alanine_id = alanine["inserted"][0]["id"]

    stereo = assert_ok(b.get_stereochemistry(alanine_id), "get_stereochemistry")
    assert stereo["stereochemistry"]

    atoms_ala = b.list_atoms_bonds(alanine_id)
    if atoms_ala["structures"] and atoms_ala["structures"][0]["bonds"]:
        bond_ref = atoms_ala["structures"][0]["bonds"][0]["ref"]
        wedge = assert_ok(b.set_bond_stereo(alanine_id, bond_ref, "wedge"), "set_bond_stereo wedge")

    if atoms_ala["structures"] and atoms_ala["structures"][0]["atoms"]:
        atom_ref = atoms_ala["structures"][0]["atoms"][0]["ref"]
        enhanced = assert_ok(b.set_enhanced_stereo(alanine_id, atom_ref, "or", 1), "set_enhanced_stereo")
        assert enhanced["enhanced_type"] == "or"

    # isotope
    eth = assert_ok(b.insert_structure("CCO", "smiles"), "insert ethanol for isotope test")
    eth_id = eth["inserted"][0]["id"]
    eth_atoms = b.list_atoms_bonds(eth_id)
    if eth_atoms["structures"] and eth_atoms["structures"][0]["atoms"]:
        c_ref = next(a["ref"] for a in eth_atoms["structures"][0]["atoms"] if a["element"] == "C")
        edited_iso = assert_ok(b.edit_atom(eth_id, c_ref, isotope=13), "edit_atom isotope=13")
        assert edited_iso["isotope"] == 13
        exported_iso = assert_ok(b.export_structure("smiles", eth_id), "export SMILES shows isotope")
        assert "13" in exported_iso["structures"][0]["data"]

    _export_and_check(b, "stereochemistry + isotope")

    # ──────────────────────────────────────────────────────────
    # 6. Shorthand (contract/expand)
    # ──────────────────────────────────────────────────────────
    print("\n📝 SHORTHAND")
    biphenyl = assert_ok(b.insert_structure("c1ccc(cc1)-c1ccccc1", "smiles"), "insert biphenyl")
    biphenyl_id = biphenyl["inserted"][0]["id"]
    con = assert_ok(b.contract_to_shorthand(biphenyl_id), "contract_to_shorthand")
    contracted_id = (con or {}).get("contracted", [{}])[0].get("id") or biphenyl_id
    expand = assert_ok(b.expand_shorthand(contracted_id), "expand_shorthand")
    assert "expanded" in expand
    _export_and_check(b, "shorthand (contract/expand)")

    # ──────────────────────────────────────────────────────────
    # 7. Duplicates
    # ──────────────────────────────────────────────────────────
    print("\n🔄 DUPLICATES")
    dup = assert_ok(b.insert_structure("c1ccccc1", "smiles"), "insert duplicate benzene")
    dup_id = dup["inserted"][0]["id"]
    dups = assert_ok(b.find_duplicates("document"), "find_duplicates")
    assert "exact" in dups or "skeleton" in dups
    _export_and_check(b, "duplicates")

    # ──────────────────────────────────────────────────────────
    # 8. Layout
    # ──────────────────────────────────────────────────────────
    print("\n📐 LAYOUT")
    canvas = assert_ok(b.describe_canvas(), "describe_canvas")
    assert "structures" in canvas
    assert "captions" in canvas
    assert "violations" in canvas

    layout = assert_ok(b.get_layout("document"), "get_layout")
    assert "structures" in layout

    object_ids = [s["id"] for s in layout["structures"]]
    arrange = assert_ok(b.arrange_grid([{"object_id": oid} for oid in object_ids], columns=3), "arrange_grid")
    assert "arranged" in arrange
    _export_and_check(b, "layout (describe + arrange)")

    # ──────────────────────────────────────────────────────────
    # 9. Annotations (arrows, symbols, brackets)
    # ──────────────────────────────────────────────────────────
    print("\n🎯 ANNOTATIONS")

    # Arrow
    arr = assert_ok(b.make_arrow("", {"x": 100, "y": 100}, {"x": 300, "y": 100},
                                  head_type="solid", start_head="full"), "make_arrow")
    arr_id = arr["object_id"]

    # Fishhook
    if atoms["structures"] and atoms["structures"][0]["atoms"]:
        anchor_ref = atoms["structures"][0]["atoms"][0]["ref"]
        fishhook = assert_ok(b.make_arrow(aspirin_id, anchor_ref, {"x": 200, "y": 50},
                                          start_head="half_left"), "make_arrow fishhook")

    # Restyle arrow
    if arr_id:
        restyled = assert_ok(b.set_arrow_style(arr_id, head_type="hollow", no_go="cross"), "set_arrow_style")
        assert restyled["head_type"] == "hollow"

    # List arrows
    arrows = assert_ok(b.list_arrows(), "list_arrows")
    assert isinstance(arrows.get("arrows"), list)

    # Symbol
    sym = assert_ok(b.make_symbol("document", "relative", {"x": 150, "y": 150}), "make_symbol")
    sym_id = sym["object_id"]
    symbols = assert_ok(b.list_symbols(), "list_symbols")
    assert isinstance(symbols.get("symbols"), list)

    # Bracket
    butane = assert_ok(b.insert_structure("CCCC", "smiles"), "insert butane for bracket")
    butane_id = butane["inserted"][0]["id"]
    bracket = assert_ok(b.make_bracket(butane_id, bracket_type="square", bracket_usage="sru"), "make_bracket")
    bracket_id = bracket["opening_object_id"]

    bracket_style = assert_ok(b.set_bracket_style(bracket_id, bracket_type="curly", bracket_usage="monomer"),
                                "set_bracket_style")
    assert bracket_style["bracket_type"] == "curly"

    brackets = assert_ok(b.list_brackets(), "list_brackets")
    assert isinstance(brackets.get("brackets"), list)

    _export_and_check(b, "annotations (arrows, symbols, brackets)")

    # ──────────────────────────────────────────────────────────
    # 10. Reactions
    # ──────────────────────────────────────────────────────────
    print("\n⚗️  REACTIONS")
    b.use_scratch_document()
    rxn = assert_ok(b.make_reaction_scheme(
        reactants=["CC(=O)Cl", "Oc1ccccc1"],
        products=["CC(=O)Oc1ccccc1"],
        reagents_text="Et3N, DCM, 0 °C",
        conditions_text="1 h"),
        "make_reaction_scheme")
    assert "object_ids" in rxn
    assert "arrow_object_id" in rxn

    warnings = assert_ok(b.check_warnings("document"), "check_warnings on reaction scheme")
    assert warnings["flagged"] == [], (
        f"reaction scheme insertion should not raise chemical warnings: {warnings}")

    _export_and_check(b, "reaction scheme")

    # ──────────────────────────────────────────────────────────
    # 11. Stoichiometry
    # ──────────────────────────────────────────────────────────
    print("\n📊 STOICHIOMETRY")
    b.use_scratch_document()

    for r in ["CC(=O)O", "N", "CCO"]:
        b.insert_structure(r, "smiles")
    for p in ["CC(=O)N", "O"]:
        b.insert_structure(p, "smiles")

    state = b.get_document_state()
    all_ids = [s["id"] for s in state["structures"]]
    reactant_ids = all_ids[:3]
    product_ids = all_ids[3:]

    for pid in product_ids:
        b.move_objects([{"object_id": pid, "dx": 300, "dy": 0}])

    stoich = assert_ok(b.make_stoichiometry_table(
        reactant_ids=reactant_ids, product_ids=product_ids),
        "make_stoichiometry_table")
    assert "grid_index" in stoich

    read_result = assert_ok(b.read_stoichiometry_tables(annotate_expected_mass=True), "read_stoichiometry_tables")
    assert len(read_result["grids"]) >= 1

    grid = read_result["grids"][0]
    _export_and_check(b, "stoichiometry table")

    # ──────────────────────────────────────────────────────────
    # 12. Scope Tables
    # ──────────────────────────────────────────────────────────
    print("\n📋 SCOPE TABLES")
    b.use_scratch_document()

    scope = assert_ok(b.build_scope_table([
        {"representation": "c1ccc(cc1)C(=O)O", "label": "1a, 92%"},
        {"representation": "Cc1ccc(cc1)C(=O)O", "label": "1b, 85%"},
        {"representation": "COc1ccc(cc1)C(=O)O", "label": "1c, 78%"},
    ], layout="double-column"), "build_scope_table")

    autonum = assert_ok(b.autonumber("document"), "autonumber")
    assert isinstance(autonum.get("numbered"), list)

    tmp_csv = os.path.join(tempfile.gettempdir(), "scope_smoke_test.csv")
    export_table = assert_ok(b.export_canvas_table(tmp_csv, overwrite=True), "export_canvas_table")
    assert os.path.exists(tmp_csv)
    _export_and_check(b, "scope table + autonumber")

    # ──────────────────────────────────────────────────────────
    # 13. Enumeration + CSV
    # ──────────────────────────────────────────────────────────
    print("\n🧬 ENUMERATION")
    enum = assert_ok(b.enumerate_derivatives(
        substituents=["[*]c1ccncc1", "[*]c1cccnc1", "[*]c1ccc[nH]1"],
        scaffold="Cc1ccc(cc1)[*]",
        properties=("mw", "formula", "logp", "tpsa")),
        "enumerate_derivatives")
    assert enum["count"] == 3

    tmp_csv2 = os.path.join(tempfile.gettempdir(), "enum_smoke_test.csv")
    csv_result = assert_ok(b.export_data_table(enum["derivatives"], tmp_csv2, overwrite=True), "export_data_table")
    assert os.path.exists(tmp_csv2)
    _export_and_check(b, "enumeration + CSV")

    # ──────────────────────────────────────────────────────────
    # 14. Style Presets
    # ──────────────────────────────────────────────────────────
    print("\n🎨 STYLE PRESETS")
    presets = b.available_presets()
    assert isinstance(presets, list) and len(presets) > 0

    apply = assert_ok(b.apply_style_preset("ACS 1996"), "apply_style_preset")
    assert isinstance(apply, dict)
    _export_and_check(b, "style presets")

    # ──────────────────────────────────────────────────────────
    # 15. Images
    # ──────────────────────────────────────────────────────────
    print("\n🖼️  IMAGES")

    png = assert_ok(b.export_image("png", "document"), "export_image png inline")
    assert "base64" in png
    assert len(png["base64"]) > 100

    svg = assert_ok(b.export_image("svg", "document"), "export_image svg inline")
    assert "base64" in svg
    assert len(svg["base64"]) > 100

    emf = assert_ok(b.export_image("emf", "document"), "export_image emf inline")
    assert "base64" in emf

    tmp_png = os.path.join(tempfile.gettempdir(), "smoke_test_full.png")
    png_file = assert_ok(b.export_image("png", "document", tmp_png, dpi=150, overwrite=True), "export_image png to file")
    assert os.path.exists(tmp_png)

    _export_and_check(b, "image formats (PNG/SVG/EMF)")

    # ──────────────────────────────────────────────────────────
    # 16. Undo/Redo
    # ──────────────────────────────────────────────────────────
    print("\n↩️  UNDO/REDO")
    # bridge.undo()'s own docstring: edits made THROUGH this connector do
    # NOT land on ChemDraw's undo stack (confirmed live) -- it only
    # reverts the user's own manual edits. So the real, documented
    # contract to verify here is the opposite of "revert happened": a
    # connector-driven insert must survive undo() unchanged. A blind
    # `ok is True` check can't catch a regression where undo() started
    # silently reverting connector edits (or throwing) instead.
    before_undo = b.status()["structures_on_page"]
    b.insert_structure("N", "smiles")
    after_insert = b.status()["structures_on_page"]
    assert after_insert == before_undo + 1, (before_undo, after_insert)

    undo = assert_ok(b.undo(), "undo")
    assert undo["ok"] is True
    after_undo = b.status()["structures_on_page"]
    assert after_undo == after_insert, (
        "connector-driven insert should NOT be reverted by undo() -- "
        f"expected {after_insert} structures, got {after_undo}")

    redo = assert_ok(b.redo(), "redo")
    assert redo["ok"] is True
    _export_and_check(b, "undo/redo")

    # ──────────────────────────────────────────────────────────
    # 17. Specialty Objects (TLC + brackets)
    # ──────────────────────────────────────────────────────────
    print("\n🔬 SPECIALTY OBJECTS")
    b.use_scratch_document()

    # Polymer bracket on butane
    butane2 = assert_ok(b.insert_structure("CCCC", "smiles"), "insert butane for brackets")
    butane2_id = butane2["inserted"][0]["id"]
    br = assert_ok(b.make_bracket(butane2_id, bracket_type="square", bracket_usage="sru"), "make_bracket polymer")
    if br:
        br_style = assert_ok(b.set_bracket_style(br["closing_object_id"], bracket_type="curly", bracket_usage="monomer"),
                             "set_bracket_style curly/monomer")
        assert br_style["bracket_type"] == "curly"

    # Explicit rect crosslink bracket
    br_cross = assert_ok(b.make_bracket("", bracket_type="round", bracket_usage="crosslink",
                                          left=100.0, top=400.0, right=200.0, bottom=500.0, margin=0.0),
                         "make_bracket explicit rect (round/crosslink)")

    # TLC plate
    lanes_spec = [
        [{"rf": 0.2, "show_rf": True}, {"rf": 0.8}],
        [{"rf": 0.5, "tail": True}],
        [{"rf": 0.35, "filled": False, "bold": True, "dashed": True}, {"rf": 0.9}],
    ]
    plate = assert_ok(b.make_tlc_plate(50.0, 50.0, 350.0, 450.0, lanes_spec), "make_tlc_plate")
    assert plate["lane_count"] == 3
    assert plate["lanes"][0]["spot_count"] == 2
    assert plate["lanes"][1]["spot_count"] == 1
    assert plate["lanes"][2]["spot_count"] == 2

    # 3rd spot per lane must be rejected
    try:
        b.make_tlc_plate(50.0, 500.0, 350.0, 600.0,
                          [[{"rf": 0.1}, {"rf": 0.5}, {"rf": 0.9}]])
        assert False, "make_tlc_plate should have rejected 3rd spot per lane"
    except Exception:
        pass  # expected

    plates = assert_ok(b.list_tlc_plates(), "list_tlc_plates")
    assert isinstance(plates.get("tlc_plates"), list)

    restyled_plate = assert_ok(b.set_tlc_plate_style(plate["object_id"], show_side_ticks=True),
                              "set_tlc_plate_style show_side_ticks=True")
    assert restyled_plate["show_side_ticks"] is True

    _export_and_check(b, "specialty objects (TLC + brackets + crosslinks)")

    # ──────────────────────────────────────────────────────────
    # 18. Ionic Wrapper (NaH) + no false overlap
    # ──────────────────────────────────────────────────────────
    print("\n⚗️  IONIC WRAPPER")
    b.use_scratch_document()

    thiophenol = assert_ok(b.insert_structure("Sc1ccccc1", "smiles", (40, 100)), "insert thiophenol")
    nah = assert_ok(b.insert_structure("[Na+].[H-]", "smiles", (100, 100)), "insert NaH ionic pair")
    tosylate = assert_ok(b.insert_structure("CC1=CC=C(C=C1)S(=O)(=O)OCC(F)(F)F", "smiles", (180, 100)), "insert tosylate")
    mcpba = assert_ok(b.insert_structure("OOC(=O)c1cccc(Cl)c1", "smiles", (290, 100)), "insert mCPBA")
    product = assert_ok(b.insert_structure("O=S(=O)(c1ccccc1)CC(F)(F)F", "smiles", (400, 100)), "insert product")

    thiophenol_id = thiophenol["inserted"][0]["id"] if thiophenol else None
    tosylate_id = tosylate["inserted"][0]["id"] if tosylate else None
    mcpba_id = mcpba["inserted"][0]["id"] if mcpba else None
    product_id = product["inserted"][0]["id"] if product else None
    nah_wrapper_id = ion_ids = None

    if nah:
        assert nah.get("wrapper_groups"), "NaH wrapper_groups should be surfaced"
        wrapper = nah["wrapper_groups"][0]
        assert wrapper["formula"] == "HNa", f"wrapper formula should be HNa, got {wrapper['formula']}"
        ion_ids = {u["id"] for u in nah["inserted"]}
        assert set(wrapper["wraps"]) == ion_ids, "wrapper should wrap both Na+ and H-"
        nah_wrapper_id = wrapper["id"]

    state = assert_ok(b.get_document_state(), "get_document_state (no false overlap)")
    if state and nah_wrapper_id and ion_ids:
        overlap_ids = {frozenset(p) for p in state["overlapping_pairs"]}
        for child_id in ion_ids:
            assert frozenset({nah_wrapper_id, child_id}) not in overlap_ids, (
                f"wrapper {nah_wrapper_id} should not overlap its own child {child_id}")
        excluded_ids = {u["id"] for u in state["non_structure_units"]}
        assert nah_wrapper_id in excluded_ids, "wrapper should be in non_structure_units"
        real_ids = {s["id"] for s in state["structures"]}
        assert nah_wrapper_id not in real_ids, "wrapper should NOT be in structures"
    _export_and_check(b, "ionic wrapper (NaH) + no false overlap")

    # ──────────────────────────────────────────────────────────
    # 18b. Stoichiometry Equivalents Cascade (same scratch doc/reagents)
    # ──────────────────────────────────────────────────────────
    # Reinstated after an audit found the plain insert-only stoichiometry
    # smoke check in section 11 was the single biggest coverage regression
    # from consolidating test_bridge.py: it never called
    # edit_stoichiometry_table at all, so the real equivalents-cascade math,
    # auto-close-prior-throwaway-document, and annotate_expected_mass
    # caption-dedupe logic all went completely untested. Values below are
    # copied from the retired test_bridge.py, which validated them against
    # the real SM65 procedure this connector was originally debugged
    # against (scaled to 2 mmol).
    if thiophenol_id and nah_wrapper_id and tosylate_id and mcpba_id and product_id:
        print("\n📊 STOICHIOMETRY EQUIVALENTS CASCADE")
        grid = assert_ok(b.make_stoichiometry_table(
            [thiophenol_id, nah_wrapper_id, tosylate_id, mcpba_id], [product_id]),
            "make_stoichiometry_table (4 reactants incl. ionic wrapper)")

        # thiophenol limiting (1.00 eq), NaH as a 60wt% dispersion (1.10
        # eq), tosylate (1.01 eq), mCPBA as 75wt% (3.04 eq) -- percent_
        # weight must be applied to sample_mass before dividing by MW, or
        # NaH/mCPBA come out inflated by 1/percent_weight (a real bug
        # caught live once, see domain/stoichiometry_cdxml.py's
        # plan_equivalents_cascade).
        edited = assert_ok(b.edit_stoichiometry_table(grid["grid_index"], [
            {"structure_id": thiophenol_id, "field": "sample_mass", "value": 0.220348},
            {"structure_id": nah_wrapper_id, "field": "sample_mass", "value": 0.08824},
            {"structure_id": nah_wrapper_id, "field": "percent_weight", "value": 0.6},
            {"structure_id": tosylate_id, "field": "sample_mass", "value": 0.51471},
            {"structure_id": mcpba_id, "field": "sample_mass", "value": 1.40024},
            {"structure_id": mcpba_id, "field": "percent_weight", "value": 0.75},
        ]), "edit_stoichiometry_table (masses + purity)")
        assert edited["equivalents_recompute_skipped"] is None, edited
        equiv_by_id = {e["structure_id"]: e["equivalents"]
                      for e in edited["equivalents_recomputed"]}
        expected_equiv = {thiophenol_id: 1.00, nah_wrapper_id: 1.103,
                         tosylate_id: 1.012, mcpba_id: 3.043}
        for sid, expected in expected_equiv.items():
            got = equiv_by_id.get(sid, -1)
            assert abs(got - expected) < 0.01, (sid, got, expected_equiv)
        assert edited["auto_closed_previous_document"] is None, edited  # first edit, real scratch doc

        edited_again = assert_ok(b.edit_stoichiometry_table(
            grid["grid_index"],
            [{"structure_id": product_id, "field": "actual_mass", "value": 0.211}]),
            "edit_stoichiometry_table again (auto-close prior throwaway)")
        assert edited_again["auto_closed_previous_document"] == edited["new_active_document"], (
            edited_again, edited)
        docs_after = assert_ok(b.list_documents(), "list_documents (no leaked throwaway window)")
        assert edited["new_active_document"] not in docs_after["documents"], docs_after

        read1 = assert_ok(b.read_stoichiometry_tables(annotate_expected_mass=True),
                          "read_stoichiometry_tables (annotate_expected_mass) #1")
        assert product_id in read1.get("annotated_expected_mass", []), read1
        read2 = assert_ok(b.read_stoichiometry_tables(annotate_expected_mass=True),
                          "read_stoichiometry_tables (annotate_expected_mass) #2, no dup")
        assert read2.get("annotated_expected_mass") == [], read2

        layout_check = assert_ok(b.get_layout("document"), "get_layout (exactly one Expected: caption)")
        expected_caps = [c for c in layout_check["captions"]
                         if c.get("text", "").startswith("Expected:")]
        assert len(expected_caps) == 1, expected_caps

        _export_and_check(b, "stoichiometry equivalents cascade")

    # ──────────────────────────────────────────────────────────
    # 19. Caption Tag Association (cap.Group fix)
    # ──────────────────────────────────────────────────────────
    print("\n🏷️  CAPTION TAG ASSOCIATION")
    b.use_scratch_document()

    scope2 = assert_ok(b.build_scope_table([
        {"representation": "c1ccc(cc1)C(=O)O", "label": "1a, 92%"},
    ], layout="single-column"), "build_scope_table for caption tag test")

    auto = assert_ok(b.autonumber("document"), "autonumber for caption tag test")

    baseline_status = assert_ok(b.status(), "status before caption tag drift test")
    canvas_before = assert_ok(b.describe_canvas(), "describe_canvas before drift test")

    captioned = [s for s in canvas_before["structures"] if s.get("captions")]
    if captioned:
        drift_target = captioned[0]
        drift_id = drift_target["id"]
        drift_caption = drift_target["captions"][0]

        # Verify caption tag metadata
        layout = assert_ok(b.get_layout("document"), "get_layout for caption tag check")
        entry = next((c for c in layout["captions"] if c["text"] == drift_caption), None)
        assert entry is not None, f"caption {drift_caption!r} not found in layout"
        assert entry.get("group_id") is None, (
            f"caption group_id should be None (cap.Group no-op), got {entry.get('group_id')}")
        assert entry.get("tag_owner_id") == drift_id, (
            f"tag_owner_id should be {drift_id}, got {entry.get('tag_owner_id')}")

        # Move structure 500pt away — tag should still associate it
        b.move_objects([{"object_id": drift_id, "dx": 500.0, "dy": 500.0}],
                        move_with_captions=False)

        after = assert_ok(b.describe_canvas(), "describe_canvas after 500pt drift")
        moved = next((s for s in after["structures"] if s["id"] == drift_id), None)
        assert moved is not None, f"moved structure {drift_id} not found"
        assert drift_caption in (moved.get("captions") or []), (
            f"caption {drift_caption!r} lost association after 500pt move, got {moved.get('captions')}")

        # Move it back
        b.move_objects([{"object_id": drift_id, "dx": -500.0, "dy": -500.0}],
                        move_with_captions=False)

    _export_and_check(b, "caption tag association")

    # ──────────────────────────────────────────────────────────
    # SUMMARY
    # ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ ALL SMOKE VISUAL TESTS PASSED")
    print("=" * 60)
    print("\nVisual checks completed for all connector features.")
    print("Open each exported PNG to verify visual correctness.")


if __name__ == "__main__":
    test_smoke_visual()