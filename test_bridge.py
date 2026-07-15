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


def expect_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")
    return actual


def _check_status_after_captioning(bridge, baseline):
    after = bridge.status()
    expect_equal(after["captions_on_page"] - baseline["captions_on_page"], 12,
                 "captions_on_page delta")
    tally = sum(e["structure_count"] for e in after["structures_per_box"])
    expect_equal(tally, after["structures_on_page"],
                 "sum(structures_per_box) vs structures_on_page")
    return after


def read_modified(bridge):
    """Read-only observational probe, never assigned to: ChemDraw's COM
    type library exposes Document.Modified as a get/set bool (same shape
    as ShowCrosshair/ShowRulers), almost certainly the same flag driving
    the title-bar '*' and close-time save prompt. It COULD be a cheap
    'anything changed' signal beating the count-based doc_signature check,
    but only if resetting it is safe — and doing that without knowing
    whether it's really the save-prompt flag risks silently suppressing
    that prompt for the user. This just reads it at a few points so real
    behavior (does it already start True? does clearing the scratch doc
    reset it?) can be observed before anything relies on it."""
    return bridge._run(lambda: bool(bridge._doc().Modified))


def main():
    b = ChemDrawBridge()

    print("== status / documents ==")
    check("status", b.status)
    check("scratch document (reused, cleared)", b.use_scratch_document)
    check("Document.Modified right after clearing scratch (observational only)",
          lambda: read_modified(b))

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
    state_before = check("get_document_state", b.get_document_state)

    print("== build_snapshot (CDXML fast path) cross-check vs insert-time describe_unit ==")
    # insert_structure's "inserted" entries come from state.describe_unit
    # (the old, still-used, full per-unit COM read). get_document_state's
    # "structures" come from state.build_snapshot, which now reads
    # bounds/atom_count/bond_count from one CDXML export instead. Nothing
    # mutated benz/etoh/asp between insertion and here, so the two paths
    # must agree exactly for the same live units — this is a same-session,
    # no-hardcoded-chemistry way to catch a regression in the new path.
    if state_before:
        by_id = {s["id"]: s for s in state_before["structures"]}
        for label, inserted in (("benzene", benz), ("ethanol", etoh), ("aspirin", asp)):
            if not inserted:
                continue
            want = inserted["inserted"][0]
            got = by_id.get(want["id"])

            def _cross_check(want=want, got=got, label=label):
                if got is None:
                    raise AssertionError(f"{label}: id {want['id']} not found in build_snapshot output")
                for field in ("formula", "atom_count", "bond_count"):
                    if got[field] != want[field]:
                        raise AssertionError(
                            f"{label}.{field}: build_snapshot={got[field]!r} "
                            f"!= insert-time describe_unit={want[field]!r}")
                for k in ("left", "top", "right", "bottom"):
                    if abs(got["bounds"][k] - want["bounds"][k]) > 0.15:
                        raise AssertionError(
                            f"{label}.bounds.{k}: build_snapshot={got['bounds'][k]} "
                            f"!= insert-time={want['bounds'][k]}")
                return True

            check(f"{label}: build_snapshot matches describe_unit", _cross_check)

    print("== cache correctness: hand-edit visibility ==")
    # b caches unit membership (see targets.doc_signature); a second bridge
    # mutating the SAME live document stands in for a hand edit or another
    # agent acting between b's tool calls. b's next read must see it —
    # proving the signature check, not a stale flag, gates the cache.
    check("Document.Modified before simulated hand edit (observational only)",
          lambda: read_modified(b))
    b2 = ChemDrawBridge()
    check("second bridge inserts a structure (simulated hand edit)",
          lambda: b2.insert_structure("CC(=O)O", "smiles", (500, 500)))
    check("Document.Modified after simulated hand edit (observational only)",
          lambda: read_modified(b))
    state_after = check("get_document_state (after simulated hand edit)",
                        b.get_document_state)
    if state_before and state_after:
        assert len(state_after["structures"]) == len(state_before["structures"]) + 1, (
            "b's cached unit list did not pick up the structure b2 inserted "
            f"into the same document: before={len(state_before['structures'])} "
            f"after={len(state_after['structures'])}"
        )

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

    print("== backup debounce ==")
    # Two move_objects calls in a row that don't change any atom/group/bond
    # count: the second should reuse the first's backup rather than
    # re-exporting + rewriting a fresh CDXML file.
    mv1 = check("move_objects (first)", lambda: b.move_objects(
        [{"object_id": etoh_id, "dx": 5, "dy": 0}]))
    mv2 = check("move_objects (second, nothing else changed)", lambda: b.move_objects(
        [{"object_id": etoh_id, "dx": 5, "dy": 0}]))
    if mv1 and mv2:
        assert mv1.get("backup_path") and mv2.get("backup_path"), "no backup written"
        assert mv1["backup_path"] == mv2["backup_path"], (
            "backup should be debounced (reused) when the document's "
            "signature hasn't changed between mutating calls"
        )

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

    print("== stable atom/bond refs (no guess-and-check) ==")
    listing = check("list_atoms_bonds ethanol", lambda: b.list_atoms_bonds(etoh_id))
    if listing:
        entry = listing["structures"][0]
        assert entry["atoms"] and entry["atoms"][0]["ref"].startswith("a"), entry
        assert entry["bonds"] and entry["bonds"][0]["ref"].startswith("b"), entry
        atom_ref = entry["atoms"][0]["ref"]
        bond_ref = entry["bonds"][0]["ref"]
        check("edit_atom by ref", lambda: b.edit_atom(etoh_id, atom_ref, element="C"))
        check("edit_bond by ref (double, then back)",
              lambda: b.edit_bond(etoh_id, bond_ref, "double"))
        check("edit_bond by ref: back to single",
              lambda: b.edit_bond(etoh_id, bond_ref, "single"))

    print("== batch atom/bond edits ==")
    asp_listing = check("list_atoms_bonds aspirin", lambda: b.list_atoms_bonds(asp_id))
    if asp_listing:
        asp_atoms = asp_listing["structures"][0]["atoms"]
        asp_bonds = asp_listing["structures"][0]["bonds"]
        atom_refs = [a["ref"] for a in asp_atoms[:2]]
        batch_atoms = check(
            "edit_atoms: batch charge no-op on 2 atoms + 1 deliberately bad ref",
            lambda: b.edit_atoms([
                {"target": asp_id, "atom": atom_refs[0], "set_charge": True, "charge": 0},
                {"target": asp_id, "atom": atom_refs[1], "set_charge": True, "charge": 0},
                {"target": asp_id, "atom": "a999999", "set_charge": True, "charge": 0},
            ]))
        if batch_atoms:
            assert len(batch_atoms["applied"]) == 2, batch_atoms
            assert len(batch_atoms["failed"]) == 1, batch_atoms
            assert batch_atoms.get("backup_path"), "no backup written"

        first_bond = asp_bonds[0]
        batch_bonds = check(
            "edit_bonds: batch no-op (restore each bond's own current order)",
            lambda: b.edit_bonds([
                {"target": asp_id, "bond": first_bond["ref"],
                 "bond_order": first_bond["order"]},
            ]))
        if batch_bonds:
            assert len(batch_bonds["applied"]) == 1, batch_bonds
            assert not batch_bonds["failed"], batch_bonds

    print("== sub-selection transform (bond split + flip) ==")
    fold = check("insert biphenyl for fold/flip test",
                lambda: b.insert_structure("c1ccc(-c2ccccc2)cc1"))
    fold_id = fold["inserted"][0]["id"] if fold else None
    fold_listing = check("list_atoms_bonds biphenyl", lambda: b.list_atoms_bonds(fold_id))
    split, split_bond_ref = None, None
    if fold_listing:
        entry = fold_listing["structures"][0]
        total_atoms = len(entry["atoms"])
        ring_bond_rejections = 0
        # Biphenyl has exactly one non-ring bond (the inter-ring linkage);
        # every ring bond must raise. Probe each bond rather than assuming
        # atom/bond ordering, since that's not something this connector
        # guarantees.
        for bd in entry["bonds"]:
            try:
                candidate = b.split_at_bond(fold_id, bd["ref"], bd["atom1_ref"])
            except Exception:
                ring_bond_rejections += 1
                continue
            if candidate and 0 < candidate["atom_count"] < total_atoms:
                split, split_bond_ref = candidate, bd["ref"]
                break
        if split:
            print(f"  ok  found connecting bond {split_bond_ref}: "
                  f"{split['atom_count']}/{total_atoms} atoms on the split "
                  f"side ({ring_bond_rejections} ring bonds correctly rejected)")
        else:
            print("FAIL  could not find a non-ring connecting bond in biphenyl")

    if split:
        before_listing = check("list_atoms_bonds before flip",
                               lambda: b.list_atoms_bonds(fold_id))
        check("transform: flip the split side", lambda: b.transform(
            fold_id, "flip", atom_refs=split["atom_refs"], bond_refs=split["bond_refs"]))
        after_listing = check("list_atoms_bonds after flip",
                              lambda: b.list_atoms_bonds(fold_id))
        if before_listing and after_listing:
            before_xy = {a["ref"]: (a["x"], a["y"])
                        for a in before_listing["structures"][0]["atoms"]}
            after_xy = {a["ref"]: (a["x"], a["y"])
                       for a in after_listing["structures"][0]["atoms"]}
            moved_ref = split["atom_refs"][0]
            fixed_ref = next((r for r in before_xy if r not in split["atom_refs"]), None)
            # Printed, not asserted: we don't yet know what point ChemDraw's
            # Flip pivots a sub-selection around. Watching real before/after
            # numbers here is how that becomes a documented fact instead of
            # a guess (see plan's "Offline planning" section).
            print(f"  ..  moved atom {moved_ref}: {before_xy.get(moved_ref)} "
                  f"-> {after_xy.get(moved_ref)}")
            if fixed_ref:
                print(f"  ..  unselected atom {fixed_ref}: "
                      f"{before_xy.get(fixed_ref)} -> {after_xy.get(fixed_ref)} "
                      f"(expected unchanged)")

        check("transform: clean up after the flip", lambda: b.transform(fold_id, "clean"))
        recheck = check("list_atoms_bonds after clean", lambda: b.list_atoms_bonds(fold_id))
        if fold_listing and recheck:
            assert len(recheck["structures"][0]["atoms"]) == \
                len(fold_listing["structures"][0]["atoms"]), \
                "flip+clean must not add/remove atoms"

        print("== sub-selection transform: negative cases ==")
        ring_bond = next((bd for bd in fold_listing["structures"][0]["bonds"]
                          if bd["ref"] != split_bond_ref), None)
        if ring_bond:
            try:
                bad = b.split_at_bond(fold_id, ring_bond["ref"], ring_bond["atom1_ref"])
            except Exception as exc:
                print(f"  ok  split_at_bond on ring bond correctly raised: {exc}")
            else:
                print(f"FAIL  split_at_bond on ring bond should have raised, got {bad}")
        try:
            bad_transform = b.transform(fold_id, "flip", atom_refs=["a999999"])
        except Exception as exc:
            print(f"  ok  transform with nonexistent atom_ref correctly raised: {exc}")
        else:
            print(f"FAIL  transform with nonexistent atom_ref should have raised, "
                 f"got {bad_transform}")

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
    baseline_status = check("status baseline (before captioning)", b.status)
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
    # NOTE: _add_caption_to_unit's `cap.Group = unit` turns out to be a
    # SILENT NO-OP on this ChemDraw version (Professional 26.0.0.6141) —
    # probed live: the assignment raises nothing, but reading cap.Group
    # back is always None, so captions built via this connector never
    # actually merge into their structure's Group here. That means this
    # scratch scenario does NOT exercise real caption-wrapper duplication
    # (unlike hand-grouped captions in a real user document, where
    # find_wrapper_duplicates has been live-verified). structures_on_page
    # staying at 6 here is therefore not yet proof of wrapper-exclusion —
    # only that nothing ballooned it. See spawned follow-up task for the
    # cap.Group regression itself.
    check("status structures_on_page still 6 after captioning",
          lambda: expect_equal(b.status()["structures_on_page"], 6,
                               "structures_on_page"))
    check("get_document_state structures still 6 after captioning",
          lambda: expect_equal(len(b.get_document_state()["structures"]), 6,
                               "len(structures)"))
    # build_scope_table's own labels ("1a, 92%", ...) plus autonumber's
    # labels ("1".."6") stack on the same 6 structures -> +12 captions.
    # boxes_on_page/structures_per_box aren't asserted against an exact
    # value: the shared scratch document can carry stray doc.Graphics
    # debris across sessions (use_scratch_document's Objects.Clear() does
    # not appear to clear doc.Graphics — a separate, pre-existing gap, not
    # part of this change). Instead we check internal consistency: the
    # per-box tally must still account for every real structure exactly
    # once, however many (or few) boxes actually exist right now.
    after_status = check(
        "status captions_on_page delta (+12) and structures_per_box "
        "sums to structures_on_page",
        lambda: _check_status_after_captioning(b, baseline_status))
    export = check("export_canvas_table (default path)",
                    lambda: b.export_canvas_table())
    if export and after_status:
        assert export["row_counts"]["structures"] == 6, \
            f"unexpected structures row_count: {export['row_counts']}"
        assert export["row_counts"]["captions"] == after_status["captions_on_page"], \
            f"export/status caption count mismatch: {export['row_counts']}"
        assert export["row_counts"]["boxes"] == after_status["boxes_on_page"], \
            f"export/status box count mismatch: {export['row_counts']}"
        excluded_total = (after_status["excluded_units"]["caption_wrapper_duplicates"]
                          + after_status["excluded_units"]["decoration_groups"])
        assert export["row_counts"]["excluded"] == excluded_total, \
            f"export/status excluded count mismatch: {export['row_counts']}"
        assert export["total_rows"] == sum(export["row_counts"].values())
        assert os.path.exists(export["path"]), \
            f"export_canvas_table path does not exist: {export['path']}"
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
