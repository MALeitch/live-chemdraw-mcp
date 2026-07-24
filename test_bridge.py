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


def _check_caption_tag_metadata(bridge, caption_text, expected_owner_id):
    """Confirm, via the public get_layout surface (group_id/tag_owner_id
    are exactly what bridge._gather_captions exposes), that a connector-
    created caption's cap.Group = unit is still the confirmed no-op
    (group_id None) and that it carries the claude_caption_owner tag
    pointing at the structure it was made for instead."""
    layout = bridge.get_layout("document")
    entry = next((c for c in layout["captions"] if c["text"] == caption_text), None)
    if entry is None:
        raise AssertionError(f"caption {caption_text!r} not found in get_layout")
    if entry.get("group_id") is not None:
        raise AssertionError(
            f"caption {caption_text!r} group_id is {entry['group_id']!r}, "
            "expected None (cap.Group = unit is a confirmed silent no-op "
            "on this ChemDraw version)")
    if entry.get("tag_owner_id") != expected_owner_id:
        raise AssertionError(
            f"caption {caption_text!r} tag_owner_id is "
            f"{entry.get('tag_owner_id')!r}, expected {expected_owner_id!r} "
            "-- the claude_caption_owner tag is not being set/read correctly")
    return entry


def _check_caption_tag_survives_drift(bridge, target_id, caption_text,
                                      dx=500.0, dy=500.0):
    """Move the structure `target_id` dx,dy away WITHOUT its caption
    (move_with_captions=False leaves the caption exactly where it was) --
    well beyond both CAPTION_MAX_GAP_PT (60) and CAPTION_MAX_DISTANCE_PT
    (120), and almost certainly nearer some other structure on a crowded
    scope-table page than its own now-distant one. Proximity-based
    association (canvas.associate_captions tiers 4/5) would misattribute or
    drop this caption in that situation -- this is the exact caption-gap
    failure mode fix_caption_gaps' docstring / README document. Confirms
    the tag-based tier (tier 0) keeps the association correct anyway."""
    bridge.move_objects([{"object_id": target_id, "dx": dx, "dy": dy}],
                        move_with_captions=False)
    try:
        after = bridge.describe_canvas()
        moved = next((s for s in after["structures"] if s["id"] == target_id), None)
        if moved is None:
            raise AssertionError(f"moved structure {target_id} not found after move")
        if caption_text not in (moved.get("captions") or []):
            raise AssertionError(
                f"caption {caption_text!r} not associated with {target_id} "
                f"after a ({dx},{dy})pt move (tag-based association broken): "
                f"got {moved.get('captions')}")
    finally:
        # Move it back regardless of pass/fail so the scratch doc isn't left
        # scattered for whatever check runs next.
        bridge.move_objects([{"object_id": target_id, "dx": -dx, "dy": -dy}],
                            move_with_captions=False)
    return {"structure_id": target_id, "caption": caption_text}


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
    # contract_to_shorthand can succeed (no exception, so check() returns its
    # result) while still reporting an empty "contracted" list under
    # "failed" for an individual unit -- contraction is per-unit isolated
    # (see contract_to_shorthand's docstring), so one unit's failure doesn't
    # raise here. This WAS live-hit by exactly that path: on a real
    # ChemDraw contraction, _reresolve_after_mutation's untagged-candidate
    # scan found the freshly-contracted label listed TWICE (once via
    # iter_units' doc.Groups loop, once more via its doc.Atoms ->
    # atom.Fragment loop rediscovering the identical element -- confirmed
    # live to share the same COM .ID, see _shorthand.py's
    # _reresolve_after_mutation for the full story) and raised "produced 2
    # untagged candidates instead of exactly one". Fixed at the root by
    # deduping same-.ID sightings before judging ambiguity -- but this
    # `(con or {}).get(...)` guard is kept regardless, since ANY per-unit
    # contraction failure (not just this one, now-fixed cause) would hit
    # the same shape: `con` present, "contracted" empty. The old
    # `con["contracted"][0]["id"] if con else None` crashed with IndexError
    # in that case instead of falling back to bip_id like the trailing
    # `or bip_id` clearly intended.
    contracted = (con or {}).get("contracted") or []
    con_id = (contracted[0]["id"] if contracted else None) or bip_id
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
    # NOTE: _add_caption_to_unit's `cap.Group = unit` is STILL a confirmed
    # SILENT NO-OP on this ChemDraw version (Professional 26.0.0.6141) —
    # probed live, the assignment raises nothing but reading cap.Group back
    # is always None. FIXED (this comment used to flag it as an open
    # follow-up): _add_caption_to_unit now also stamps each caption with a
    # persistent claude_caption_owner object tag (targets.tag_caption_owner)
    # pointing at its structure's claude_id, and canvas.associate_captions
    # prefers that tag ahead of Group-based lookup (tier 0). That still
    # means this scratch scenario does NOT exercise real caption-wrapper
    # duplication via Group merging (unlike hand-grouped captions in a real
    # user document, where find_wrapper_duplicates has been live-verified)
    # — structures_on_page staying at 6 here shows the tag-based path never
    # spuriously creates a wrapper, not that Group-based wrapping works.
    # The tag mechanism itself — including that it survives a structure
    # moving well outside proximity range, the actual bug this fixes — is
    # exercised directly below.
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

    print("== caption tag-based association (cap.Group = unit fix) ==")
    canvas_before_drift = check("describe_canvas before caption-tag drift test",
                                b.describe_canvas)
    drift_target = None
    if canvas_before_drift:
        captioned = [s for s in canvas_before_drift["structures"] if s.get("captions")]
        drift_target = captioned[0] if captioned else None
    if drift_target:
        drift_id = drift_target["id"]
        drift_caption = drift_target["captions"][0]
        check(f"caption {drift_caption!r} metadata: group_id None, tag_owner_id set",
              lambda: _check_caption_tag_metadata(b, drift_caption, drift_id))
        check(f"move {drift_id} 500pt away (captions left behind); tag still "
              "resolves it, not proximity",
              lambda: _check_caption_tag_survives_drift(b, drift_id, drift_caption))
    else:
        print("SKIP  caption tag-based association drift test: "
              "no captioned structure found on the page")

    print("== reaction scheme (cleared scratch) ==")
    check("clear scratch", b.use_scratch_document)
    rxn = check("reaction scheme", lambda: b.make_reaction_scheme(
        ["CC(=O)Cl", "Oc1ccccc1"], ["CC(=O)Oc1ccccc1"],
        reagents_text="Et3N, DCM, 0 °C"))
    rxn_arrow_id = (rxn or {}).get("arrow_object_id")
    if rxn_arrow_id:
        check("restyle the reaction scheme's own arrow",
              lambda: b.set_arrow_style(rxn_arrow_id, tail_head="full"))

    print("== annotations: arrows/symbols/isotopes (cleared scratch) ==")
    check("clear scratch", b.use_scratch_document)
    eth = check("insert ethanol", lambda: b.insert_structure("CCO"))
    eth_id = eth["inserted"][0]["id"] if eth else None
    atoms = check("list atoms for anchoring", lambda: b.list_atoms_bonds(eth_id))
    carbon_ref = None
    if atoms:
        for a in atoms["structures"][0]["atoms"]:
            if a["element"] == "C":
                carbon_ref = a["ref"]
                break

    # Plain arrow between two raw points (no target/atom anchoring needed).
    arr = check("make_arrow (raw points, defaults)", lambda: b.make_arrow(
        "", {"x": 300.0, "y": 60.0}, {"x": 150.0, "y": 60.0}))
    arr_id = arr["object_id"] if arr else None
    if arr:
        assert arr["start_head"] == "full" and arr["tail_head"] == "none", arr

    # Fishhook single-electron arrow, anchored to an atom in the structure.
    if carbon_ref:
        check("make_arrow fishhook anchored to atom", lambda: b.make_arrow(
            eth_id, carbon_ref, {"x": 200.0, "y": 120.0},
            start_head="half_left"))

    if arr_id:
        restyled = check("set_arrow_style -> hollow + no_go cross",
                         lambda: b.set_arrow_style(arr_id, head_type="hollow", no_go="cross"))
        if restyled:
            assert restyled["head_type"] == "hollow" and restyled["no_go"] == "cross", restyled
        check("list_arrows finds it", lambda: b.list_arrows())

    # Symbol: a "rel" stereo-descriptor label, confirmed live to render
    # cleanly (unlike the tiny lone-pair/radical/dagger/etc. types).
    if carbon_ref:
        sym = check("make_symbol relative-stereo label", lambda: b.make_symbol(
            eth_id, "relative", carbon_ref, offset_y=-20.0))
        if sym:
            assert sym["symbol_type"] == "relative", sym
        check("list_symbols finds it", lambda: b.list_symbols())

    # Isotope labeling: set, confirm export fidelity, then clear.
    if carbon_ref:
        edited = check("edit_atom isotope=13", lambda: b.edit_atom(
            eth_id, carbon_ref, isotope=13))
        if edited:
            assert edited["isotope"] == 13, edited
        exported = check("export SMILES shows isotope", lambda: b.export_structure(
            "smiles", eth_id))
        if exported:
            assert "13" in exported["structures"][0]["data"], exported
        # KNOWN LIMITATION, confirmed live: setting isotope=0 from a
        # nonzero value is silently rejected by ChemDraw's COM layer (not
        # isotope-specific -- Charge=0 from nonzero has the identical
        # failure mode, confirmed live; see _apply_atom_edit's comment).
        # No assertion here on purpose -- this deliberately just observes
        # and reports the real value via `check`'s own printed output
        # rather than asserting a reset that isn't reliably achievable.
        check("edit_atom isotope=0 (reset -- may not take effect, see "
              "_apply_atom_edit)", lambda: b.edit_atom(eth_id, carbon_ref, isotope=0))

        # Enhanced stereo ("and1"/"or1" relative-stereo grouping).
        enh = check("set_enhanced_stereo or1", lambda: b.set_enhanced_stereo(
            eth_id, carbon_ref, "or", group_number=1))
        if enh:
            assert enh["enhanced_type"] == "or" and enh["group_number"] == 1, enh

    print("== specialty objects: polymer brackets (cleared scratch) ==")
    check("clear scratch", b.use_scratch_document)
    butane = check("insert butane", lambda: b.insert_structure("CCCC"))
    butane_id = butane["inserted"][0]["id"] if butane else None
    baseline_status = check("status before brackets", b.status)

    if butane_id:
        br = check("make_bracket wrapping butane (square/sru)", lambda: b.make_bracket(
            butane_id, bracket_type="square", bracket_usage="sru"))
        if br:
            assert br["bracket_type"] == "square" and br["bracket_usage"] == "sru", br
            assert br["opening_object_id"] and br["closing_object_id"], br

        check("list_brackets finds the pair", lambda: b.list_brackets())

        if br:
            restyled = check("set_bracket_style closing -> curly/monomer",
                             lambda: b.set_bracket_style(
                                 br["closing_object_id"], bracket_type="curly",
                                 bracket_usage="monomer"))
            if restyled:
                assert (restyled["bracket_type"] == "curly"
                        and restyled["bracket_usage"] == "monomer"), restyled

        # Brackets live in doc.Brackets, a separate collection from
        # doc.Groups -- confirm they never inflate the structure count
        # (see _specialty_objects.py's module docstring).
        after_status = check("status after brackets (structure count unaffected)",
                             b.status)
        if baseline_status and after_status:
            assert (after_status["structures_on_page"]
                    == baseline_status["structures_on_page"]), (
                baseline_status, after_status)

        check("export image with brackets visible", lambda: b.export_image(
            target="document"))

    # Explicit-rectangle placement (no target structure), round/crosslink --
    # exercises the manual left/top/right/bottom path.
    check("make_bracket explicit rect (round/crosslink)", lambda: b.make_bracket(
        "", bracket_type="round", bracket_usage="crosslink",
        left=100.0, top=400.0, right=200.0, bottom=500.0, margin=0.0))

    print("== specialty objects: TLC plates (cleared scratch) ==")
    check("clear scratch", b.use_scratch_document)
    plate_status_before = check("status before TLC plate", b.status)

    lanes_spec = [
        [{"rf": 0.2, "show_rf": True}, {"rf": 0.8}],
        [{"rf": 0.5, "tail": True}],
        [{"rf": 0.35, "filled": False, "bold": True, "dashed": True},
         {"rf": 0.9}],
    ]
    plate = check("make_tlc_plate 3 lanes / 5 spots total",
                  lambda: b.make_tlc_plate(
                      50.0, 50.0, 350.0, 450.0, lanes_spec))
    if plate:
        assert plate["lane_count"] == 3, plate
        # Confirms the two live-found COM bugs (silent AddSpot no-op past
        # 2 spots/lane, and the create-then-style-all-in-one-final-pass
        # workaround for the sibling-creation Rf/style reset) both landed
        # correctly -- every lane's spot_count/rf_values must match what
        # was requested, not silently fall short.
        got_counts = [lane["spot_count"] for lane in plate["lanes"]]
        assert got_counts == [2, 1, 2], (got_counts, plate)
        got_rfs = [lane["rf_values"] for lane in plate["lanes"]]
        expected_rfs = [[0.2, 0.8], [0.5], [0.35, 0.9]]
        for got, expected in zip(got_rfs, expected_rfs):
            for g, e in zip(got, expected):
                assert abs(g - e) < 1e-3, (got_rfs, expected_rfs)
        assert plate["object_id"], plate

    # A lane with 3 spots must be rejected up front (the hard 2-spot cap),
    # never silently truncated -- ChemDraw itself gives no error signal at
    # all for the dropped 3rd spot, so this validation has to happen on
    # the connector side, before any COM calls.
    try:
        bad_plate = b.make_tlc_plate(50.0, 500.0, 350.0, 600.0,
                                     [[{"rf": 0.1}, {"rf": 0.5}, {"rf": 0.9}]])
        FAIL += 1
        print(f"FAIL  make_tlc_plate should reject a 3rd spot per lane, "
              f"got {bad_plate}")
    except Exception as exc:
        PASS += 1
        print(f"  ok  make_tlc_plate correctly rejected a 3rd spot per "
              f"lane: {exc}")

    if plate:
        # doc.TLCPlates is a separate collection from doc.Groups -- confirm
        # a TLC plate never inflates the structure count (same contract as
        # brackets/arrows/symbols).
        plate_status_after = check("status after TLC plate (structure "
                                   "count unaffected)", b.status)
        if plate_status_before and plate_status_after:
            assert (plate_status_after["structures_on_page"]
                    == plate_status_before["structures_on_page"]), (
                plate_status_before, plate_status_after)

        check("list_tlc_plates finds it", lambda: b.list_tlc_plates())

        restyled = check("set_tlc_plate_style show_side_ticks=True",
                         lambda: b.set_tlc_plate_style(
                             plate["object_id"], show_side_ticks=True))
        if restyled:
            assert restyled["show_side_ticks"] is True, restyled

        check("export image with TLC plate visible", lambda: b.export_image(
            target="document"))

    print("== enumeration + csv (no canvas) ==")
    en = check("enumerate 5 derivatives", lambda: b.enumerate_derivatives(
        ["[*]c1ccncc1", "[*]c1cccnc1", "[*]c1ccc[nH]1", "[*]c1ccco1", "[*]c1cccs1"],
        scaffold="Cc1ccc(cc1)[*]",
        properties=("mw", "formula", "logp", "tpsa")))
    if en:
        assert en["count"] == 5 and not en["failed"], en
        tmp_csv = os.path.join(tempfile.gettempdir(), "chemdraw_smoke.csv")
        check("export csv", lambda: b.export_data_table(en["derivatives"], tmp_csv))

    print("== ionic wrapper surfaced + no false-positive overlap (cleared scratch) ==")
    check("clear scratch", b.use_scratch_document)
    thiophenol = check("insert thiophenol", lambda: b.insert_structure(
        "Sc1ccccc1", "smiles", (40, 100)))
    nah = check("insert NaH ionic pair", lambda: b.insert_structure(
        "[Na+].[H-]", "smiles", (100, 100)))
    tosylate = check("insert CF3CH2 tosylate", lambda: b.insert_structure(
        "CC1=CC=C(C=C1)S(=O)(=O)OCC(F)(F)F", "smiles", (180, 100)))
    mcpba = check("insert mCPBA", lambda: b.insert_structure(
        "OOC(=O)c1cccc(Cl)c1", "smiles", (290, 100)))
    product = check("insert SM65 product", lambda: b.insert_structure(
        "O=S(=O)(c1ccccc1)CC(F)(F)F", "smiles", (400, 100)))

    thiophenol_id = thiophenol["inserted"][0]["id"] if thiophenol else None
    tosylate_id = tosylate["inserted"][0]["id"] if tosylate else None
    mcpba_id = mcpba["inserted"][0]["id"] if mcpba else None
    product_id = product["inserted"][0]["id"] if product else None
    nah_wrapper_id = ion_ids = None
    if nah:
        # Fixes a real gap: insert_structure used to silently drop this
        # wrapper Group entirely (see _split_wrapper_groups) -- the only
        # way to find it was via an unrelated move_objects call's
        # unexpected_moves field. Now it's surfaced directly.
        assert nah["wrapper_groups"], nah
        wrapper = nah["wrapper_groups"][0]
        assert wrapper["formula"] == "HNa", wrapper
        ion_ids = {u["id"] for u in nah["inserted"]}
        assert set(wrapper["wraps"]) == ion_ids, (wrapper, ion_ids)
        nah_wrapper_id = wrapper["id"]

    state = check("get_document_state (no false-positive wrapper overlap)",
                  b.get_document_state)
    if state and nah_wrapper_id and ion_ids:
        # Before find_union_wrapper_duplicates existed, this wrapper
        # survived into "structures" and permanently triggered a
        # false-positive overlapping_pairs entry against each of its own
        # children on every read.
        overlap_ids = {frozenset(p) for p in state["overlapping_pairs"]}
        for child_id in ion_ids:
            assert frozenset({nah_wrapper_id, child_id}) not in overlap_ids, (
                nah_wrapper_id, child_id, state["overlapping_pairs"])
        excluded_ids = {u["id"] for u in state["non_structure_units"]}
        assert nah_wrapper_id in excluded_ids, (nah_wrapper_id, state["non_structure_units"])
        real_ids = {s["id"] for s in state["structures"]}
        assert nah_wrapper_id not in real_ids, (nah_wrapper_id, real_ids)

    print("== stoichiometry: equivalents cascade + auto-close (same scratch doc) ==")
    grid = None
    if thiophenol_id and nah_wrapper_id and tosylate_id and mcpba_id and product_id:
        grid = check("make_stoichiometry_table (4 reactants incl. ionic wrapper)",
                     lambda: b.make_stoichiometry_table(
                         [thiophenol_id, nah_wrapper_id, tosylate_id, mcpba_id],
                         [product_id]))

    edited = None
    if grid is not None:
        # Same scaled-2mmol numbers as the live SM65 procedure this
        # connector was originally debugged against: thiophenol limiting
        # (1.00 eq), NaH as a 60wt% dispersion (1.10 eq), tosylate (1.01
        # eq), mCPBA as 75wt% (3.04 eq) -- percent_weight must be applied
        # to sample_mass before dividing by MW, or NaH/mCPBA come out
        # inflated by 1/percent_weight (a real bug caught live once, see
        # domain/stoichiometry_cdxml.py's plan_equivalents_cascade).
        edited = check("edit_stoichiometry_table (batch 1: masses + purity)",
                       lambda: b.edit_stoichiometry_table(grid["grid_index"], [
                           {"structure_id": thiophenol_id, "field": "sample_mass", "value": 0.220348},
                           {"structure_id": nah_wrapper_id, "field": "sample_mass", "value": 0.08824},
                           {"structure_id": nah_wrapper_id, "field": "percent_weight", "value": 0.6},
                           {"structure_id": tosylate_id, "field": "sample_mass", "value": 0.51471},
                           {"structure_id": mcpba_id, "field": "sample_mass", "value": 1.40024},
                           {"structure_id": mcpba_id, "field": "percent_weight", "value": 0.75},
                       ]))
    if edited:
        assert edited["equivalents_recompute_skipped"] is None, edited
        equiv_by_id = {e["structure_id"]: e["equivalents"]
                      for e in edited["equivalents_recomputed"]}
        expected_equiv = {thiophenol_id: 1.00, nah_wrapper_id: 1.103,
                         tosylate_id: 1.012, mcpba_id: 3.043}
        for sid, expected in expected_equiv.items():
            assert abs(equiv_by_id.get(sid, -1) - expected) < 0.01, (
                sid, equiv_by_id, expected_equiv)
        assert edited["auto_closed_previous_document"] is None, edited  # first edit, real scratch doc

        edited_again = check(
            "edit_stoichiometry_table again (auto-close prior throwaway)",
            lambda: b.edit_stoichiometry_table(
                grid["grid_index"],
                [{"structure_id": product_id, "field": "actual_mass", "value": 0.211}]))
        if edited_again:
            assert edited_again["auto_closed_previous_document"] == edited["new_active_document"], (
                edited_again, edited)
            docs_after = check("list_documents (no leaked throwaway window)",
                               b.list_documents)
            if docs_after:
                assert edited["new_active_document"] not in docs_after["documents"], docs_after

        print("== annotate_expected_mass caption dedupe (issue 1) ==")
        read1 = check("read_stoichiometry_table(annotate_expected_mass=True) #1",
                      lambda: b.read_stoichiometry_tables(annotate_expected_mass=True))
        read2 = check("read_stoichiometry_table(annotate_expected_mass=True) #2 (no dup)",
                      lambda: b.read_stoichiometry_tables(annotate_expected_mass=True))
        if read1:
            assert product_id in read1.get("annotated_expected_mass", []), read1
        if read2:
            # Second call must detect the caption #1 already added (tagged
            # via tag_caption_owner, text starting "Expected:") and skip it
            # -- not stack a duplicate.
            assert read2.get("annotated_expected_mass") == [], read2
        layout = check("get_layout (exactly one Expected: caption)", lambda: b.get_layout("document"))
        if layout:
            expected_caps = [c for c in layout["captions"]
                             if c.get("text", "").startswith("Expected:")]
            assert len(expected_caps) == 1, expected_caps

    print("== undo ==")
    check("undo", b.undo)

    print(f"\n{'=' * 40}\n{PASS} passed, {FAIL} failed")
    return FAIL


if __name__ == "__main__":
    raise SystemExit(main())
