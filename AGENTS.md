# AGENTS.md — live-chemdraw-mcp development notes

This file is for anyone reading the source in `chemdraw_connector/` --
AI coding assistants and human contributors alike, whether you're
extending it, debugging something, or just want to understand why it's
built the way it is. Not for end users of the MCP server (see
`README.md` for that): none of this matters if you're only talking to
the server through Claude, since it exists precisely to hide these
details from that caller. It covers the package layout and, more
importantly, the ChemDraw COM quirks this connector was built around:
most of these were only found by driving live ChemDraw and watching it
misbehave, and re-deriving them from scratch is expensive. Read this
before working in `chemdraw_connector/` for any reason.

## Architecture

```
chemdraw_connector/
  com/           COM plumbing only: worker (single STA thread, timeouts,
                 wedge detection), connection (attach/relaunch/reconnect),
                 types (COM enums <-> readable vocabulary)
  domain/        pure logic, zero COM imports, pytest-covered
  bridge/        the seam: every tool call goes through here
    __init__.py  composes ChemDrawBridge from the mixins below
    _annotations.py, _document_session.py, _enumeration.py, _layout.py,
    _manipulation.py, _plumbing.py, _properties_qc.py, _reaction.py,
    _selection.py, _shorthand.py, _specialty_objects.py, _state_diff.py,
    _stereochemistry.py, _structure_io.py,
    _style.py    one focused mixin per concern (document lifecycle,
                 layout, shorthand contraction, stereochemistry...)
  targets.py     structure addressing (tags, selection, doc) + safe
                 doc-scoped atom/bond access
  state.py       canvas snapshots for get_document_state / diff
  snapshots.py   automatic .cdxml backups before batch operations
tools/           thin MCP tool definitions
server.py        FastMCP entry point (stdio)
```

`docs/com_typelib/` — a live COM reflection dump of ChemDraw's full type
library (interfaces/methods/enums), plus the reusable probe scripts that
generated it. This is the source of truth for COM surface confirmed to exist
but not yet implemented in this connector (native stoichiometry grids,
Markush/alt-groups, as of the 2026-07-21 capture — mechanism/reaction arrow
types, atom isotope labeling, reaction symbols, enhanced-stereo flags,
polymer brackets, and TLC plates are now implemented, see `README.md`'s
Capabilities section) — see its own README for details.

`bridge/` used to be a single `bridge.py`; it was split into a package once
the original file grew past 2,000 lines with ~90 methods covering unrelated
concerns. Every consumer (`tools/*.py`, `server.py`, `test_smoke_visual.py`)
still calls `bridge.<method_name>(...)` as a flat attribute access — mixin
composition in `__init__.py` makes every method directly callable on the
`ChemDrawBridge` instance regardless of which file defines it, so the split
is invisible from outside the package and the "every tool call goes
through here" property is unchanged.

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
- Worse than the above: `chemdraw_transform(action='clean')` on a
  circle-drawn polyheteroaromatic system (confirmed live on a 20-structure
  scope table built from lowercase-aromatic SMILES) can silently rewrite
  whole rings to all-single-bond skeletons AND introduce dangling-valence
  radical nitrogens (`[N]` with no charge/H, invalid chemistry) — worse than
  just losing the circle, this corrupts the actual bond orders. ~75% of
  structures in that test were affected; simple monocyclic rings were often
  fine, fused/heteroatom-dense ones were not. Root-caused to
  `enumeration.validate_smiles`/`validate_molblock` handing ChemDraw
  lowercase aromatic text in the first place — **fixed** by Kekulizing
  (`Chem.Kekulize(..., clearAromaticFlags=True)` +
  `kekuleSmiles=True`/`kekulize=True`) every SMILES/molfile before it's
  inserted, so ChemDraw only ever receives explicit double bonds and never
  draws the circle at all. Structures already on a page from before this
  fix (or hand-drawn with ChemDraw's own aromatic-ring tool) can still carry
  circles — clean those with `chemdraw_contract_group`'s kekulize path or by
  hand, and never trust `action='clean'` on one without re-exporting SMILES
  afterward to confirm bond orders/valence survived.
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
- A query call (`chemdraw_get_document_state`/`chemdraw_status`/
  `chemdraw_describe_canvas`/`chemdraw_select`) immediately after ANY
  mutating call can observe stale `doc.Groups`/`Atoms`/`Bonds` counts for
  exactly one call — the per-document unit-list cache (`targets.py`'s
  `doc_signature`-gated cache, see `_cache_for`) is only as fresh as the
  live COM properties it reads, and ChemDraw's own internal state (window
  focus, `ActiveDocument`) is confirmed to lag a COM call that otherwise
  "landed" correctly (see `com/nudge.py`'s `bring_to_foreground`
  docstring). Every document-lifecycle mutation already calls
  `bring_to_foreground` afterward to settle this; `chemdraw_insert_
  structure` was the one gap (fixed 2026-07-24, see
  `bridge/_structure_io.py`). If a NEW mutating call is ever added here,
  give it the same settle step and explicitly invalidate
  `self._cache_for(doc)` (`targets._invalidate_cache`) rather than relying
  on the next call's own signature check to notice — don't assume the
  signature check alone is enough, since it can be fooled by exactly this
  kind of transient staleness.
- `IChemDrawObjects.Rotate(degrees, True)` on a whole unit is safe for
  chemistry (a rigid in-plane rotation can't create/destroy stereocenters)
  and always ends up axis-aligned, so its bounding box's width/height are an
  exact swap after a net 90 degrees of rotation — but don't trust that
  swap blindly for packing math; re-read `Left/Top/Right/Bottom` live after
  rotating, same as any other geometry read here. `.Flip(vertical, False)`
  is NOT chemistry-safe: probed live on a drawn (S)-alanine, a horizontal
  flip silently inverted its stereocenter to R — ChemDraw mirrors the 2D
  depiction but does not re-derive wedge/hash bond geometry to compensate,
  so the wedge stays `wedge-begin` pointing at what is now the wrong
  configuration. `chemdraw_arrange_in_region`'s `flip_ids` therefore reads
  each flipped unit's CIP descriptors before/after and reports any change
  in `violations.stereo_changed` — always check it before trusting a flip
  on real data; a chiral structure that flips clean is the exception, not
  the default.
- `Caption.Position` is NOT the caption's top-left corner. Probed live:
  positioning a caption at `structure_bottom + 4.0` still left ~5pt of
  visible overlap with the structure above it — every caption-placing gap
  in the codebase (`layout_math.caption_anchor`'s `label_gap`, used by
  `arrange_grid`/`build_scope_table`; `autonumber`'s literal) now defaults
  to `12.0` instead, which gives real clearance. No existing tool can
  reposition an EXISTING caption independently either: captions never get a
  `claude_id`/`object_id` (unlike structures), so `arrange_in_region`/
  `move_objects` can only ever carry a caption by the same delta its
  structure moved, which preserves a bad offset rather than fixing it.
  `fix_caption_gaps` recomputes the offset from scratch (center-x,
  `bottom + gap`, default `gap=12.0`) using `_gather_captions`/
  `_set_position` directly. Its default proximity-based caption-to-structure association
  (`canvas.associate_captions`) is unreliable if a caption was ever left
  behind while its structure moved elsewhere (e.g. `move_objects` with
  `move_with_captions=False`) — proximity will confidently attach it to
  whichever structure is now nearest, which is wrong. Use `fix_caption_gaps`'s
  `pairs` param (`{structure_id: caption_text}`, captured from
  `describe_canvas` BEFORE any such move) to bypass association via exact
  text match instead.
- `chemdraw_transform(action='clean')` can drastically RESIZE a badly-drawn
  structure, not just tidy bond angles — probed live, one structure went
  from 101x84pt to 134x55pt, another grew ~50% taller. Any layout computed
  before a clean (or contract/expand) call is stale garbage afterward; do
  not patch around it, re-read bounds fresh and replan.
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
- **Bulk structure operations belong in the domain layer, not one COM call
  per structure.** If a tool needs to touch every structure on a large page
  (clean, contract/expand shorthand, dense packing, ...), read/write CDXML
  directly in `domain/` and touch ChemDraw once at the boundary (a single
  open/save/export), rather than looping a live per-object COM call over
  many units in one `_run` closure — that pattern reliably wedges the
  worker on a big enough document (see the "never loop a mutating COM call"
  entry above). `domain/highlight_cdxml.py`, `domain/cdx_binary.py`, and
  `domain/dense_pack.py` all independently converged on this shape.
- A path currently open in ChemDraw is OS-locked for writes from anywhere
  else (including a plain external Python script, not just this connector)
  until it's closed in ChemDraw first — an external write against an
  open-in-ChemDraw path fails with a bare `PermissionError` that gives no
  hint why.
- `chemdraw_save_document` is **not geometry-lossless** for a CDXML page
  authored outside ChemDraw with `DrawingSpace="Poster"` and explicit
  `Width`/`Height`: once opened and saved, those attributes are replaced
  with ChemDraw's own paginated-layout attributes (`BoundingBox`,
  `WidthPages`). If you need to keep reading a page's own geometry after a
  save, keep your pre-save copy as the source of truth and only push the
  ChemDraw-touched copy forward for rendering/viewing, never for further
  programmatic reads of `Width`/`Height`/`DrawingSpace`.
- `IChemDrawArrow.ArrowHeadType` does **not** take the `CDArrowType` bitmask
  its name suggests (`NoHead`/`HalfHead`/`FullHead`/`Resonance`/
  `Equilibrium`/`Hollow`/`RetroSynthetic`) — that assumption, made from
  type-library reflection alone, was tested live and found wrong before any
  bridge code was written around it. It actually takes `CDArrowHeadType`
  (`Solid`/`Hollow`/`Angle` — the arrowhead's FILL style). The real
  head-count/shape control is `ArrowHeadPositionStart`/
  `ArrowHeadPositionTail`, each taking `CDArrowHeadPositionType`
  (`None`/`Full`/`HalfLeft`/`HalfRight`) — confirmed live via a large
  `HeadSize` + 600 DPI screenshot that `HalfLeft`/`HalfRight` render a
  genuine single-barb "fishhook" arrowhead, the classic single-electron
  mechanism-arrow shape, via a completely different property than its enum
  name would suggest. `IChemDrawArrow` also does **not** share
  `IChemDrawSpline`'s `NumPoints`/`GetPoint`/`SetPoint` surface (confirmed
  live: raises `AttributeError`) — a plain Arrow is a straight 2-point
  object; setting `ArcOrigin` alone (get/put, exists on the object) does
  NOT flip the read-only `IsArc` to `True` either, so curved/arc arrows
  have no confirmed COM path yet. See `docs/com_typelib/` for the full
  reflection dump this was checked against.
- `IChemDrawSymbol.SymbolType` is **get-only** — must be fixed at creation
  via `MakeSymbol(type)`'s argument, never set afterward. `Position` and
  `Start` are linked (setting one moves the other to match, confirmed
  live). Types 10-12 (`racemic`/`absolute`/`relative`) render as clear
  boxed text labels; types 0-9 (lone pair, electron, radical cation/anion,
  circle plus/minus, dagger, double dagger, plus, minus) render at a tiny
  default size (confirmed live: bounds as small as ~0.13pt for the
  electron-dot type) — `Height`/`Width` are read-only, and a
  `doc.Objects.Scale()` call after selecting one didn't visibly resize it
  in a quick test; no confirmed way to enlarge these yet.
- `Atom.Isotope` (plain settable mass-number int) survives export as real
  isotope notation — confirmed live: SMILES `C[13CH2]O`, molfile `M  ISO`
  block — not a ChemDraw-display-only label. It's genuinely distinct from
  `Atom.IsotopicAbundance` (a separate enrichment-level enum: natural/
  enriched/deficient/nonnatural/any). **Known limitation, not solved**:
  writing `Isotope = 0` to clear a label back to natural abundance is
  silently rejected (readback keeps the prior nonzero value, no exception)
  when the atom's isotope is already nonzero — reproduced deterministically
  5/5 attempts within a single COM session, not a transient flake. This
  turns out not to be isotope-specific: `Atom.Charge = 0` has the
  **identical** rejection behavior when the atom's charge is already
  nonzero (confirmed live) — looks like a general ChemDraw COM quirk
  treating a literal `0` write as a no-op/unset sentinel rather than a real
  assignment, on at least these two atom properties. A negative value IS
  accepted and clamps to 0 (confirmed: `Isotope = -1` → readback `0`) —
  **but do not use this as a workaround**: confirmed live that
  `Atom.Isotope = -1` also corrupts that SAME atom's `Charge` to `-1` as a
  side effect (reproduced on a fresh, never-charged atom), and the
  corrupted `Charge` could then not be fixed back to `0` either (same
  0-from-nonzero rejection, recursively) — chasing the clamping trick trades
  one data-corruption bug for another. `edit_atom` writes the requested
  value plainly and always reports the real post-write value, so a caller
  can see honestly whether a reset landed instead of a workaround silently
  claiming success while corrupting charge.
- `EnhancedStereoType`/`EnhancedStereoGroupNumber` (the "and1"/"or1"
  relative-stereo notation) live directly on `IChemDrawAtom`, confirmed
  settable — a per-atom property, not document- or bond-level.
- **Polymer brackets (`IChemDrawBracket`), confirmed live 2026-07-21, several
  surprises the original plan (reflection-only) got wrong:**
  - `doc.MakeBracket(type)`/`doc.Brackets` work exactly like
    `doc.Groups`/`doc.Captions` (`.Count`/`.Item(i)`), and `MakeObjectTag`/
    `GetObjectTag` tag/round-trip on a Bracket the same as any other object.
  - `Bracket.Start`/`.End` **reject atom objects outright**
    (`Type mismatch` COM error) — plain `{X, Y}` points only, same as
    Arrow/Symbol. There is no COM-level "true structural anchoring."
  - **Big one:** one `IChemDrawBracket` is **one bracket glyph** (a single
    line/curve with hook end-caps) — **not** a full enclosing "[...]" pair.
    A real SRU/repeat-unit notation needs **two** Bracket objects (an
    opening and a closing mark); `chemdraw_make_bracket` creates both.
  - `BracketUsage` drives a real, useful, ChemDraw-generated abbreviation
    label next to the bracket — confirmed live: `sru`→"n", `monomer`→"mon",
    `crosslink`→"xl", `unspecified`→no label. This label is NOT the
    (broken, see below) `SRULabel` property; it's some other internal
    text-generation path tied directly to the usage enum.
  - `RepeatCount`, `SRULabel`, and `ComponentOrder` — confirmed live, both
    **get and put reliably raise a COM exception** on a `MakeBracket()`-
    created object, regardless of `BracketUsage`. Reflection shows get/put
    for all three; none of the three actually work over COM. No custom
    label text or explicit repeat count is achievable this way — only the
    fixed auto-generated abbreviation above.
  - `InsideAtoms`/`OutsideAtoms`/`ContainedAtoms`/`CrossingBonds` (the
    read-only "what does this bracket enclose" properties) do **not**
    reliably reflect real geometric containment — confirmed inconsistent
    across several controlled tests (a 4-atom chain with a bracket box
    wrapping only its middle two atoms reported one atom clearly OUTSIDE
    the box as `ContainedAtoms`; reversing the box's corner order didn't
    change the result either). Treat a bracket as purely decorative.
  - Hook (end-cap) direction is driven by which point is `Start` vs `End`
    (top→bottom renders hooks curling right/"["; bottom→top curls
    left/"]") — reliable for a single, lone bracket, and
    `PolymerFlipType` also reliably flips a lone bracket. **Neither is
    reliable once a second Bracket exists in the same COM session**:
    confirmed live across many controlled tests (reversing Start/End,
    toggling PolymerFlipType, swapping creation order — separately and in
    combination) that the second-created bracket sometimes renders with
    its own correct orientation and sometimes visually "sticks" to match
    the first one instead, unpredictably. Not root-caused — two brackets
    created via two separate process reattachments to the same live
    ChemDraw DID mirror correctly, so this looks like some internal
    rendering-cache quirk specific to touching multiple Brackets within
    one automation connection. `chemdraw_make_bracket` codes the
    semantically-correct opposite vectors anyway (it's right more often
    than not), but always screenshot the result — the pair may need a
    manual orientation fix in the ChemDraw UI.
  - Since `doc.Brackets` is a wholly separate collection from `doc.Groups`
    (never touched by `targets.iter_units`), a bracket never inflates
    `chemdraw_status`/`chemdraw_describe_canvas` structure counts, and
    `domain/canvas.py`'s `find_wrapper_duplicates` (tuned for
    caption-wrapper geometry) never sees or misclassifies one — confirmed
    live, no interaction at all.
- **TLC plates (`IChemDrawTLCPlate`/`Lane`/`Spot`), confirmed live
  2026-07-21, four separate COM bugs the original plan (reflection-only)
  had no way to predict:**
  - `doc.MakeTLCPlate()`/`doc.TLCPlates` work exactly like
    `doc.MakeBracket()`/`doc.Brackets`, and `doc.Objects.Clear()` (what
    `use_scratch_document` calls) confirmed to clear `doc.TLCPlates` too —
    no special-case scratch-clearing code needed, unlike `doc.Graphics`.
  - **Good news:** `IChemDrawTLCSpot` has **no** `Position`/`Bounds`/
    `Top`/`Bottom`/`Left`/`Right` at all — a spot's rendered position is
    driven entirely by its `Rf` property (0-1), linearly interpolated by
    ChemDraw itself between the plate's `OriginFraction`/
    `SolventFrontFraction` lines. Confirmed via a 5-spot, 3-lane
    screenshot: measured pixel positions for rf=0.2/0.35/0.5/0.8/0.9 all
    landed within ~1% of the expected fraction, and lanes rendered as
    evenly spaced columns automatically. No `domain/tlc_layout.py` module
    was needed — there was no real position math left to do.
  - `AddLane(retval)`/`AddSpot(retval)` **reject a true zero-arg call**
    ("Parameter not optional") despite reflection showing no real input
    parameter — a deep `GetFuncDesc`/`GetRefTypeInfo` dump (not just the
    name-only pass) showed the "retval" arg is actually typed as
    `IChemDrawTLCLane**`/`IChemDrawTLCSpot**` with IN|OUT flags but not the
    `PARAMFLAG_FRETVAL` bit that would let win32com treat it as a pure
    return value. Fix: pass a literal `None` — it satisfies the call and
    is never actually consumed; the real new object comes back as the
    normal return value.
  - **The Lane object `plate.AddLane(None)` itself returns is unreliable
    for that lane's own immediately-following `.AddSpot()` call** —
    reproduced deterministically: whichever Lane was most recently created
    (by creation order, not call order) raises a bare COM "Exception
    occurred" on `.AddSpot()` forever via that specific reference, even
    after touching unrelated objects in between. Fix, also confirmed
    live: re-fetch the same lane fresh via
    `plate.Lanes.Item(plate.Lanes.Count)` immediately after `AddLane` and
    use that reference for `AddSpot` — works immediately, every time.
  - **A single Lane can hold at most 2 spots via `AddSpot`** — confirmed
    as a hard, deterministic cap (reproduced even with full
    plate/lane/spot refetching at every step, so it isn't a staleness
    artifact). A 3rd `AddSpot()` call on a lane already at 2 spots is a
    **silent no-op**: `Spots.Count` never increases, no exception, no
    signal of failure at all — `chemdraw_make_tlc_plate` validates and
    rejects a 3rd spot per lane before making any COM calls, since
    ChemDraw itself won't tell you it silently dropped one. Relatedly:
    calling `AddSpot()` twice in a row on the same lane *without* writing
    any property to the first spot in between is *also* a silent no-op
    for the second call (the first spot stays "uncommitted" until some
    property write forces it real).
  - **Creating spot N+1 in a lane silently resets spot N's
    `Rf`/`Tail`/`Bold`/`Filled`/`Dashed` back to ChemDraw's defaults**
    (confirmed live, deterministic, every time — `ShowRf` is the only
    property observed to survive a sibling's creation). A property
    written *after* every spot in that lane already exists sticks
    permanently. So `make_tlc_plate` does a strict two-pass
    create-all-spots-then-style-all-spots sequence per lane, never
    interleaving a later `AddSpot` with an earlier spot's style — the
    reset is scoped to siblings within the same lane only (confirmed live
    that populating a different lane never disturbs an already-finalized
    one).
  - `Tail` is actually a `float` (default `0.0`; setting it to `True`
    reads back as `-1.0`, not `1.0`) — likely accepts an explicit
    tail-length value, not explored further. Its visual effect (a
    comet-tail smear) could **not** be confirmed at available
    `chemdraw_export_image` resolution (tried 200 and 600 DPI — the 600
    DPI request didn't appear to actually increase the exported pixel
    dimensions) — treat as experimental. `Filled=False` + `Dashed=True` +
    `Bold=True` **was** visually confirmed distinct (a hollow,
    dashed-outline circle vs. a plain solid dot).
  - Since `doc.TLCPlates` is a wholly separate collection from
    `doc.Groups`, a TLC plate never inflates `chemdraw_status`/
    `chemdraw_describe_canvas` structure counts — confirmed live, same as
    Brackets/Arrows/Symbols.
- **Never call `targets.unit_atoms_bonds` once per unit in a loop over
  many units.** It resolves ONE unit's atoms/bonds by scanning the WHOLE
  `doc.Atoms`/`doc.Bonds` collections and filtering by fragment tag —
  correct and necessary for a single unit (there's no COM shortcut to ask
  "just this unit's atoms" directly, see the `.Item()`-on-a-group-scoped-
  collection crash warning above), but calling it per unit over a big
  `target="document"` multiplies that full-document scan by the unit
  count. Confirmed live 2026-08-04 (issue #29): on a synthetic 200-
  structure/1200-atom document, `chemdraw_list_atoms(target="document")`
  was still running after 5+ minutes (`chemdraw_status(fast=true)`'s
  `elapsed` climbing continuously — a genuine O(units x atoms) blowup, not
  a wedge). Use `targets.bulk_unit_atoms_bonds(doc, units, cache)` instead
  for any multi-unit target — one shared pass over `doc.Atoms`/
  `doc.Bonds`, bucketed by fragment tag, paid at most once for however
  many units are cache-cold (cache-warm units are served from cache
  either way, at the same cost as the per-unit function).
- **Once the COM worker's one dedicated STA thread is stuck inside a
  blocking native call, it can NEVER recover on its own** — a blocked
  call can't be interrupted from Python (no safe way to abort a thread
  mid-syscall), so the thread never returns to its own queue to pick up
  new work (`com/worker.py`'s `_run` loop is a single `while True: fn, ...
  = self._queue.get(); ...; future.set_result(fn())` — if that `fn()`
  call never returns, that's it, forever). Confirmed live 2026-08-04:
  this genuinely never self-heals, even after the underlying ChemDraw
  process is separately killed and relaunched by hand — every future
  call still fails fast with "a previous call is still waiting on
  ChemDraw." Previously the only fix was restarting the whole connector
  process. `bridge.reset_connection(kill_process, confirm)`
  (`chemdraw_reset_connection`) fixes this without a process restart: it
  simply abandons the wedged `ComWorker`/`Connection` (the orphaned
  thread is `daemon=True`, never blocks process exit) and builds a fresh
  pair. `kill_process=False` just reattaches to the same still-running
  ChemDraw — recovers with zero disruption if the stall wasn't ChemDraw's
  own UI thread being genuinely busy; `kill_process=True` also kills (by
  the exact attached pid, avoiding the multi-instance ambiguity hazard)
  and relaunches ChemDraw for a genuine internal wedge.
- `IChemDrawDocument.name` is just the basename, not the full path — two
  open documents with the same filename in different folders (a normal
  Save-As-to-a-different-folder-then-reopen cycle) is common, not exotic,
  and any lookup keyed on `name` alone is ambiguous in that state.
  `com/doc_window.py`'s old title-text window search
  (`find_document_window`) made this worse: `EnumChildWindows`'s
  enumeration order has no guaranteed relationship to `app.Documents`'
  own COM collection order, so it could close a *different* window than
  the one just identified and Modified-checked via COM. Fix: ChemDraw is
  a classic MDI app with one `MDIClient` window per `CSWFrame`;
  `WM_MDIGETACTIVE` sent to it returns the hwnd of whichever `CSWDocument`
  child is CURRENTLY active — not by matching text, by reading ChemDraw's
  own live activation state. So `.Activate()` the correct COM Document
  object first (unambiguous — a specific object reference), bring the
  frame to the OS foreground, then read back its hwnd via
  `doc_window.active_mdi_child`. Confirmed live against a real 11-document
  session with 4 documents sharing one name: activating
  `chemdraw-mcp-scratch.cdxml` via COM and querying `WM_MDIGETACTIVE`
  returned exactly that window's hwnd. `bridge/_document_session.py`'s
  `_resolve_document` additionally refuses to guess when a caller's
  `name` alone matches 2+ open documents — it raises with every match's
  real `FullName` rather than picking the first, and accepts an optional
  `full_name` to disambiguate (`chemdraw_close_document`/
  `chemdraw_set_active_document`; `chemdraw_list_documents` flags any
  such name under `duplicate_names`). **`full_name` alone is not always
  enough** — confirmed live 2026-08-04 against a real session: 2+ open
  documents can be the exact SAME file opened as separate windows, which
  makes their `FullName` identical too, not just their `name`. `index`
  (1-based, matching `chemdraw_list_documents`' own `app.Documents.Item(i)`
  order) is the last-resort disambiguator for that case. The post-close
  poll (waiting for `Documents.Count` to actually reflect the close)
  switched from a by-name/by-FullName "is it still there" check to a
  plain count comparison for the same reason — identity-based polling has
  the identical ambiguity problem one layer down.
