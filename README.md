# chemdraw-mcp

MCP server that connects Claude to a **live ChemDraw window** on Windows via
COM automation. Claude can draw, edit, and organize structures in the document
you have open — insert by SMILES/name, build substrate-scope figures sized for
journal columns, detect and contract functional groups to shorthand inside
larger molecules (chemdraw_contract_group: Ph, TES, Boc, Ts... ~40 groups,
SMARTS-matched via RDKit), contract/expand whole-structure labels, bulk-expand
every (or just specific) shorthand label across a whole document in one call
(chemdraw_expand_labels), interpret and reorganize whole figures (
chemdraw_describe_canvas for one semantic snapshot — structures classified
apart from phantom wrapper groups, captions matched to the structures they
label, panel boxes with members, overlap/overflow violations;
chemdraw_arrange_in_region to fit structures into a panel box in one call,
captions riding along, never rescaling; chemdraw_get_layout /
chemdraw_move_objects for raw-geometry plans with automatic
collateral-movement detection), read/set
stereochemistry, generate IUPAC names and HRMS text, check for duplicates and
valence errors, apply journal style presets, build reaction schemes, and
enumerate derivative libraries with RDKit-computed properties.

Built and validated against **ChemDraw 22 (Revvity)** on Windows 11 /
Python 3.14.

## Setup

```powershell
cd chemdraw-mcp
python -m venv .venv
.venv\Scripts\python -m pip install pywin32 rdkit "mcp[cli]" pytest
```

Register in `%APPDATA%\Claude\claude_desktop_config.json`:

```json
"mcpServers": {
  "chemdraw": {
    "command": "C:\\Users\\USER\\chemdraw-mcp\\.venv\\Scripts\\python.exe",
    "args": ["C:\\Users\\USER\\chemdraw-mcp\\server.py"]
  }
}
```

Restart the Claude desktop app. No ChemDraw-side installation is needed — the
server attaches to a running ChemDraw (or launches one) over COM.

## Testing

- `.venv\Scripts\python -m pytest` — unit tests for the pure logic
  (layout math, style presets, HRMS text, dedup, numbering, diff, RDKit
  enumeration). No ChemDraw required.
- `.venv\Scripts\python test_bridge.py` — live smoke test; drives a visible
  ChemDraw window end to end. Watch it run.

## Architecture

```
chemdraw_connector/
  com/         COM plumbing only: worker (single STA thread, timeouts,
               wedge detection), connection (attach/relaunch/reconnect),
               types (COM enums <-> readable vocabulary)
  domain/      pure logic, zero COM imports, pytest-covered
  bridge.py    the seam: every tool call goes through here
  targets.py   structure addressing (tags, selection, doc) + safe
               doc-scoped atom/bond access
  state.py     canvas snapshots for get_document_state / diff
  snapshots.py automatic .cdxml backups before batch operations
tools/         thin MCP tool definitions
server.py      FastMCP entry point (stdio)
```

## Hard-won ChemDraw COM facts (do not relearn these the crash way)

- `Objects.Data` is a parameterized property: insert via raw
  `Invoke(dispid, DISPATCH_PROPERTYPUT, mime_type, payload)`. A bogus type
  string returns S_OK and inserts nothing — always verify atom count after.
- Each Data-insert creates one `Group`; the Group is the addressable
  structure handle (`grp.Objects` scopes GetData/Formula/Move/... to it).
- Atoms report `Group = None` (parent chain is atom → fragment → group);
  fragments inherit the group's object tags.
- **Never call `.Item()` on a group-scoped `Atoms`/`Bonds` collection — it
  hard-crashes the ChemDraw process.** Use doc-scoped `doc.Atoms`/`doc.Bonds`
  and filter by fragment tag (`targets.unit_atoms_bonds`).
- `app.ActiveDocument` intermittently returns `None` over COM; track the
  working document by name (`bridge._doc`).
- `ContractObjectsToLabel`/`ExpandLabelsToStructure` can rebuild the
  structure and orphan its tag — re-resolve and re-tag afterward.
- `ContractObjectsToLabel` collapses **the entire collection you hand it**
  into one label with whatever text you pass — no substructure detection, no
  validation of label vs. chemistry. To contract a group inside a molecule,
  select just that group's objects first (see next point).
- `Selection.Objects.Add(obj)` is a **silent no-op** (Count stays 0). The
  working way to build a selection programmatically: `doc.Objects.Unselect()`
  then set `obj.Selected = True` per atom/bond/graphic — `Selected` is a
  settable property on the IChemDrawObject base.
- `Document.Undo()`/`Redo()` return S_OK but **do nothing for COM-initiated
  edits** — only the user's own UI actions land on the undo stack. Rollback
  paths: the .cdxml backups, or ExpandLabelsToStructure for contractions
  (the real atoms stay nested inside a label).
- CDXML export (`text/xml`) node ids are **identical to COM `Atom.ID`**, and
  contracted nicknames stay single nodes. Molfile export silently expands
  nicknames into all their atoms. Substructure work must use CDXML.
- A ring displayed with an aromatic circle stores order-1 bonds plus a
  separate `doc.Graphics` circle object (CDXML order "1.5"). Contracting
  such a ring without first writing explicit kekulé orders degrades the
  nested fragment to its all-single-bond skeleton, and the circle survives
  as an orphaned floating graphic — kekulize the matched bonds and select
  the circle along with them (bridge._contract_atom_ids does both).
- Tool docstrings must be plain string literals: an f-string is not a
  docstring, so FastMCP registers the tool with **no description at all**.
  Pass dynamic text via `@mcp.tool(description=...)`.
- A structure whose caption is grouped with it enumerates as **two units
  with the same formula AND the same atom count**: the tight structure
  group, plus an outer wrapper group holding structure + caption. Probed
  live: six of six captioned molecules in one panel produced these phantom
  duplicates, and atom counts cannot filter them (the wrapper contains the
  same atoms). What betrays a wrapper is geometry — identical formula,
  bounds fully containing the real structure's, strictly larger (extended
  over the caption). `domain/canvas.py:find_wrapper_duplicates` implements
  the check; `chemdraw_describe_canvas` files wrappers under
  `non_structure_units`. Never target a wrapper for chemistry operations.
- Panel-title captions get spuriously adopted as structure labels by
  proximity heuristics. Probed live, both directions: a title sitting just
  ABOVE a panel's first structure, and the NEXT panel's title sitting just
  BELOW the previous panel's bottom structure (within normal label gap) —
  an arrange then dragged that title 34 pt into the wrong panel.
  `canvas.associate_captions` therefore vetoes captions entirely above a
  candidate structure and captions in a different panel box.
- Moving one structure's Group can carry along an object that was NOT
  targeted. Probed live on a real figure: an ion-pair counterion
  (drawn as its own top-level Fragment, its own `claude-*` tag, correctly
  NOT deduped into its cation's Group by `iter_units`' own tag-inheritance
  check) still moved when the cation's Group was moved — root cause not
  fully pinned down, but real and reproducible. `chemdraw_move_objects`
  defends against this generically rather than trying to special-case it:
  every call snapshots the document before and after and diffs them
  (`domain.diff`, the same logic behind `chemdraw_diff_since_last_check`),
  reporting any object that moved WITHOUT being in the requested batch as
  `unexpected_moves`. Always check that field after a move.
- For any "reorganize/move these structures" request: call
  `chemdraw_describe_canvas` ONCE (classification + relationships +
  violations in one round trip), then `chemdraw_arrange_in_region` for
  "fit these into that box" — two calls total, captions carried along.
  For layouts those two can't express, fall back to `chemdraw_get_layout`
  + offline math (`domain/layout_math.py`'s `shelf_pack` /
  `distribute_vertical` place item sizes into an existing box, reporting
  overflow rather than silently violating it) + ONE `chemdraw_move_objects`
  batch. Probing bounds one object at a time and nudging incrementally
  (the first attempt at this) is far slower and is how the box-overflow
  and counterion bugs both happened — plan first, move once, verify once.
- `Atom.LabelText` is non-empty for ANY atom ChemDraw draws with a text
  symbol — including ordinary heteroatoms like N, O, NH, not just
  contracted shorthand labels. LabelText alone cannot tell "this is a
  contracted nickname" apart from "this is how nitrogen is drawn"; use
  `Atom.NodeType` too (`com/types.py: NODE_TYPE_ORDINARY_ATOM = {0, 1}` —
  Unspecified/Element are plain atoms; Fragment=5 is what
  ContractObjectsToLabel produces, Nickname=4 is ChemDraw's own
  dictionary-typed nicknames, and there are others — anything outside
  {0, 1} is some kind of label/collapsed representation).
- Contraction and expansion behave OPPOSITELY under batching. Probed live:
  selecting the matched rings from TWO separate, unrelated molecules and
  calling `ContractObjectsToLabel` ONCE welded them into a single malformed
  fragment with a radical (open valence) — the method's job is "collapse
  everything in this selection into ONE label," so batching contraction
  across structures reproduces the original whole-document-collapse bug at
  smaller scale. `ExpandLabelsToStructure`, by contrast, expands every
  already-distinct label ATOM found in its selection independently —
  nothing gets merged, because each label is already its own separate
  object going in — so batching expansion across many structures in ONE
  call is safe and (probed) meaningfully faster: it avoids the per-call
  document-wide id-reconciliation cost (`_reresolve_after_mutation` scans
  the whole document) that a one-call-per-structure loop pays N times over.
  `bridge.expand_labels` is the bulk, single-call, whole-document version;
  `contract_to_shorthand`/`contract_functional_groups` must stay one call
  per structure/match.
- `contract_functional_groups` batches by ROUND, not by structure or match:
  one CDXML export + RDKit match per round contracts EVERY non-overlapping
  match it finds (not just the first), reusing one document-wide atom/bond/
  graphics handle-map scan across all of them, instead of rescanning per
  match. Probed live: this cut a 574s/38-contraction benchmark to
  ~2x faster in aggregate on a mixed-match-count set (avg 1.5 matches/
  structure), with individual 3-match structures seeing much larger
  per-structure gains (round trip dropped from ~45s to ~1.3s) — the
  aggregate number is diluted by 0/1-match structures, which see no benefit
  from batching since they only ever needed one round anyway. The one
  empirically-unverified assumption this relies on (cached atom/bond
  handles from before an earlier match's contraction remain valid for a
  later, disjoint match in the same round) held with zero fallback
  triggers across a 40-structure/61-contraction live test — but the code
  still defends against it being false (`_StaleHandleMap`, rebuild-and-
  retry) rather than assuming it always will.
- Never loop a mutating COM call (ContractObjectsToLabel,
  ExpandLabelsToStructure, ...) over many units inside one `_run` closure.
  Probed live: looping over all 45 structures of a real document exceeded
  the worker's timeout + nudge grace period, so the connector reported
  "ChemDraw is not responding" and the worker wedged — even though the call
  was still legitimately running and completed correctly on its own
  afterward. `contract_to_shorthand`/`expand_shorthand`/
  `contract_functional_groups` each submit **one COM call per unit** so a
  many-structure document can't trip the wedge detector.
- `Document.Close()` is a **complete no-op** over COM (probed: every variant
  — Close(False), Close(0), Close(), Modified=False first, Activate first —
  returns S_OK and closes nothing). Never create throwaway documents: use
  `bridge.use_scratch_document()` / the `chemdraw_use_scratch_document`
  tool, which reuses one document named `chemdraw-mcp-scratch.cdxml` and
  clears it on each acquisition.
- Coordinates are points (72/inch), top-left origin; default bond length is
  14.4 pt. Backups land in `%LOCALAPPDATA%\chemdraw-mcp\backups`.

## Troubleshooting

- **In-progress canvas text edit / text tool left active** — puts ChemDraw's
  UI thread in a modal loop that blocks COM. The connector auto-recovers: on a
  timeout it posts an Escape to ChemDraw's focused control via Win32
  (`com/nudge.py`, a channel separate from the blocked COM one) and retries,
  so a stray edit no longer wedges the session. Only if that fails do you see
  the "not responding" error below.
- **"ChemDraw is not responding to automation"** — a modal dialog is open (or
  an edit the auto-nudge couldn't clear); dismiss the dialog or press Escape /
  click empty canvas, then retry.
- **"ChemDraw stopped responding during this operation"** — ChemDraw crashed
  and was relaunched; recover unsaved work from the backups folder above.
- Tools report structure ids like `claude-a1b2c3d4`; if one "no longer
  exists," the structure was deleted/rebuilt — ask Claude to call
  `chemdraw_get_document_state` to re-inventory the page.
