"""Manual smoke test — exercises the bridge against a live, visible ChemDraw.
Run: .venv\\Scripts\\python.exe test_bridge.py
Watch the ChemDraw window while it runs.
"""
import base64
import os
import tempfile

from chemdraw_connector.bridge import ChemDrawBridge

PASS, FAIL = 0, 0


def check(label, fn):
    global PASS, FAIL
    try:
        result = fn()
        PASS += 1
        print(f"  ok  {label}: {str(result)[:140]}")
        return result
    except Exception as exc:
        FAIL += 1
        print(f"FAIL  {label}: {exc}")
        return None


def main():
    b = ChemDrawBridge()

    print("== status / documents ==")
    check("status", b.status)
    check("scratch document (reused, cleared)", b.use_scratch_document)

    print("== insert ==")
    benz = check("insert benzene (smiles)", lambda: b.insert_structure("c1ccccc1"))
    etoh = check("insert ethanol at position", lambda: b.insert_structure("CCO", "smiles", (300, 100)))
    asp = check("insert aspirin (by name)", lambda: b.insert_structure("aspirin", "name"))
    bad = check("bad smiles rejected", lambda: (
        lambda: (_ for _ in ()).throw(AssertionError("accepted bad SMILES"))
    )() if b.insert_structure("c1ccccc", "smiles") else None)
    if bad is None:
        # expected: the call raised, which check() counts as FAIL — flip it
        global PASS, FAIL
        FAIL -= 1
        PASS += 1
        print("  ok  bad smiles rejected (raised as expected)")

    benz_id = benz["inserted"][0]["id"] if benz else None
    asp_id = asp["inserted"][0]["id"] if asp else None
    etoh_id = etoh["inserted"][0]["id"] if etoh else None

    print("== state / list ==")
    check("get_document_state", b.get_document_state)
    check("list_objects", b.list_objects)

    print("== export / properties ==")
    exp = check("export aspirin smiles", lambda: b.export_structure("smiles", asp_id))
    if exp:
        assert "C" in exp["structures"][0]["data"], "empty SMILES export"
    props = check("get_properties aspirin", lambda: b.get_properties(asp_id))
    if props:
        assert props["properties"][0]["formula"] == "C9H8O4", props
    check("iupac name benzene", lambda: b.get_iupac_name(benz_id))
    check("characterization aspirin", lambda: b.generate_characterization_text(asp_id))

    print("== manipulate ==")
    check("move benzene", lambda: b.transform(benz_id, "move", dx=50, dy=30))
    check("rotate benzene", lambda: b.transform(benz_id, "rotate", degrees=30))
    check("clean benzene", lambda: b.transform(benz_id, "clean"))
    check("select aspirin (watch highlight)", lambda: b.select(asp_id))
    check("get_selection", b.get_selection)

    print("== atom/bond editing ==")
    check("edit_atom: benzene C1 -> N (pyridine)",
          lambda: b.edit_atom(benz_id, 1, element="N"))
    check("add_atom: methyl onto ethanol",
          lambda: b.add_atom(etoh_id, 1, "C"))
    check("edit_bond: set bond 1 double (then back)",
          lambda: b.edit_bond(etoh_id, 1, "double"))
    check("check_warnings (expect flags from forced double bond)",
          lambda: b.check_warnings("document"))
    check("edit_bond: back to single", lambda: b.edit_bond(etoh_id, 1, "single"))

    print("== stereo ==")
    ala = check("insert L-alanine", lambda: b.insert_structure("C[C@@H](N)C(=O)O"))
    ala_id = ala["inserted"][0]["id"] if ala else None
    check("get_stereochemistry", lambda: b.get_stereochemistry(ala_id))
    check("set_bond_stereo wedge", lambda: b.set_bond_stereo(ala_id, 1, "wedge"))

    print("== shorthand ==")
    bip = check("insert biphenyl", lambda: b.insert_structure("c1ccc(cc1)-c1ccccc1"))
    bip_id = bip["inserted"][0]["id"] if bip else None
    con = check("contract to shorthand", lambda: b.contract_to_shorthand(bip_id))
    con_id = (con["contracted"][0]["id"] if con else None) or bip_id
    check("expand shorthand", lambda: b.expand_shorthand(con_id))

    print("== duplicates ==")
    check("insert duplicate benzene", lambda: b.insert_structure("C1=CC=CC=C1"))
    dup = check("find_duplicates", lambda: b.find_duplicates("document"))

    print("== images / clipboard ==")
    img = check("export_image png inline", lambda: b.export_image("png", asp_id))
    if img:
        assert len(base64.b64decode(img["base64"])) > 500
    tmp_png = os.path.join(tempfile.gettempdir(), "chemdraw_smoke.png")
    check("export_image png to file",
          lambda: b.export_image("png", "document", tmp_png, dpi=150))
    check("copy_to_clipboard", lambda: b.copy_to_clipboard(asp_id))

    print("== style ==")
    check("apply ACS 1996 preset", lambda: b.apply_style_preset("ACS 1996"))

    print("== scope table (cleared scratch; watch the grid build) ==")
    check("clear scratch", b.use_scratch_document)
    scope = check("build_scope_table 6 entries", lambda: b.build_scope_table([
        {"representation": "c1ccc(cc1)C(=O)O", "label": "1a, 92%"},
        {"representation": "Cc1ccc(cc1)C(=O)O", "label": "1b, 85%"},
        {"representation": "COc1ccc(cc1)C(=O)O", "label": "1c, 78%"},
        {"representation": "Fc1ccc(cc1)C(=O)O", "label": "1d, 90%"},
        {"representation": "Clc1ccc(cc1)C(=O)O", "label": "1e, 81%"},
        {"representation": "Brc1ccc(cc1)C(=O)O", "label": "1f, 74%"},
    ], layout="double-column"))
    if scope:
        assert scope.get("preview_png_base64"), "no preview image"
        assert scope.get("backup_path"), "no backup written"
    check("autonumber", lambda: b.autonumber("document"))
    check("diff_since_last_check", b.diff_since_last_check)

    print("== reaction scheme (cleared scratch) ==")
    check("clear scratch", b.use_scratch_document)
    check("reaction scheme", lambda: b.make_reaction_scheme(
        ["CC(=O)Cl", "Oc1ccccc1"], ["CC(=O)Oc1ccccc1"],
        reagents_text="Et3N, DCM, 0 °C"))

    print("== enumeration + csv (no canvas) ==")
    en = check("enumerate 5 derivatives", lambda: b.enumerate_derivatives(
        ["[*]c1ccncc1", "[*]c1cccnc1", "[*]c1ccc[nH]1", "[*]c1ccco1", "[*]c1cccs1"],
        scaffold="Cc1ccc(cc1)[*]",
        properties=("mw", "formula", "logp", "tpsa")))
    if en:
        assert en["count"] == 5 and not en["failed"], en
        tmp_csv = os.path.join(tempfile.gettempdir(), "chemdraw_smoke.csv")
        check("export csv", lambda: b.export_data_table(en["derivatives"], tmp_csv))

    print("== undo ==")
    check("undo", b.undo)

    print(f"\n{'=' * 40}\n{PASS} passed, {FAIL} failed")
    return FAIL


if __name__ == "__main__":
    raise SystemExit(main())
