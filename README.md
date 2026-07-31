# chemdraw-mcp

[![Tests](https://github.com/MALeitch/chemdraw-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/MALeitch/chemdraw-mcp/actions/workflows/tests.yml)

MCP server that connects Claude to a **live ChemDraw window** on Windows via
COM automation — Claude can draw, edit, and organize structures directly in
the document you have open.

**Capabilities:**
- **Insert & export** — SMILES, name, molfile, InChI, CDXML; export as an
  image, structure text, or straight to the clipboard
- **Substrate-scope tables** — build a whole scope figure in one call, sized
  for journal columns, with labels/yields placed under each structure
- **Shorthand groups** — detect and contract functional groups inline
  (`chemdraw_contract_group`: Ph, TES, Boc, Ts... ~40 groups, SMARTS-matched
  via RDKit), or contract/expand a whole structure to a single label,
  including a one-call bulk expand across the whole document
- **Figure layout** — `chemdraw_describe_canvas` gives one semantic read of a
  whole page (structures, captions matched to their owners, panel boxes,
  overlap/overflow violations); `chemdraw_arrange_in_region` fits structures
  into a panel box in one call, captions riding along, nothing ever
  rescaled; `chemdraw_get_layout`/`chemdraw_move_objects` for raw-geometry
  plans with automatic collateral-movement detection
- **Chemistry QC** — read/set stereochemistry (wedge/hash, "and1"/"or1"
  enhanced-stereo grouping), duplicate detection, valence warnings, IUPAC
  naming, HRMS text generation
- **Publication tools** — journal style presets, reaction schemes, and
  derivative-library enumeration with RDKit-computed properties
- **Annotations** — mechanism/reaction arrows (`chemdraw_make_arrow`: solid/
  hollow/angle heads, single-barb "fishhook" electron-pushing arrows,
  crossed-out "no-go" pathway markers, dipole markers) and symbols
  (`chemdraw_make_symbol`: racemic/absolute/relative stereo-descriptor
  labels; lone pairs/radicals/daggers also place but currently render at a
  tiny, apparently unscalable default size — see the tool's own docstring).
  Isotope labeling (`chemdraw_edit_atom(..., isotope=13, ...)`) survives
  export as real isotope notation, not just a ChemDraw-display label.
  **Limitation:** arrows/symbols are free-floating — moving a nearby
  structure with `chemdraw_move_objects`/`chemdraw_transform` will NOT carry
  them along, the same class of gap already documented for captions below.
  Curved/arc arrows and true double-object ⇌ equilibrium-arrow pairs aren't
  supported yet — both were probed live and need more COM investigation
  before a design commitment (see `docs/com_typelib/`)
- **Polymer repeat-unit brackets** (`chemdraw_make_bracket`: square/curly/
  round glyph, SRU/monomer/copolymer/crosslink/... usage with ChemDraw's own
  auto-generated abbreviation label — "n"/"mon"/"xl"/etc.) wraps a structure
  (or an explicit rectangle) with an opening + closing bracket pair.
  **Limitations, confirmed live, not solved:** RepeatCount/SRULabel/
  ComponentOrder cannot be set via COM at all (both get and put reliably
  raise); the pair's mirrored orientation ("[...]" vs two identically-
  oriented marks) is unreliable once a second bracket exists in one
  automation session — always check the exported image; brackets are
  purely decorative (not bound to real atom/bond membership despite
  ChemDraw exposing InsideAtoms/ContainedAtoms/CrossingBonds properties —
  confirmed these don't reflect true geometry) and share the same
  free-floating/doesn't-move-with-the-structure limitation as arrows/
  symbols above
- **TLC plates** (`chemdraw_make_tlc_plate`: rectangular plate outline with
  labeled Rf-spot lanes for reaction-monitoring figures) — a spot's vertical
  position is driven entirely by its `rf` (0-1) property, which ChemDraw
  itself interpolates between the plate's origin/solvent-front lines
  (confirmed live via measured screenshot pixel positions, within ~1% of
  the expected fraction) — no manual position math needed. Supports
  `show_rf` (auto "Rf = 0.NN" label), `filled`/`bold`/`dashed` (a hollow
  dashed-outline circle is visually confirmed distinct from a solid dot),
  and an experimental `tail` (comet-tail smear) whose visual effect could
  not be confirmed at available screenshot resolution. **Hard limit,
  confirmed live and enforced by the tool (raises before any COM call): a
  single lane can hold at most 2 spots** — ChemDraw's own `AddSpot` is a
  silent no-op past that, with no error at all. Same free-floating/
  doesn't-move-with-the-structure limitation as arrows/symbols/brackets
  above.

This README covers setup and usage — not a per-tool reference (each tool's
exact parameters, return shape, and usage notes live in its own docstring,
visible to any MCP client via its tool listing) and not internal
architecture or ChemDraw-COM implementation notes (see
[`AGENTS.md`](AGENTS.md) for those — useful reading whenever you're
looking at the source, whether you're debugging something, curious how
it works, or extending it, not only for the last one).

Built and validated against **ChemDraw 26 (Revvity)** on Windows 11 /
Python 3.14.

> This is an independent, unofficial project. It is not affiliated with,
> endorsed by, or supported by Revvity or PerkinElmer. "ChemDraw" is a
> trademark of its respective owner; it's referenced here only to describe
> compatibility. You'll need your own licensed copy of ChemDraw to use this.

## Setup

```powershell
cd chemdraw-mcp
python -m venv .venv
.venv\Scripts\python -m pip install pywin32 rdkit "mcp[cli]" pytest
```

Register in `%APPDATA%\Claude\claude_desktop_config.json` (replace
`C:\\path\\to\\chemdraw-mcp` with wherever you cloned it):

```json
"mcpServers": {
  "chemdraw": {
    "command": "C:\\path\\to\\chemdraw-mcp\\.venv\\Scripts\\python.exe",
    "args": ["C:\\path\\to\\chemdraw-mcp\\server.py"]
  }
}
```

Restart the Claude desktop app. No ChemDraw-side installation is needed — the
server attaches to a running ChemDraw (or launches one) over COM.

## Testing

- `.venv\Scripts\python -m pytest` — unit tests for the pure logic, all
  running against fakes/fixtures with no live ChemDraw required: layout
  math, style presets, HRMS text, dedup, numbering, diff, RDKit
  enumeration, CDXML parsing/graph-building, substructure/SMARTS matching,
  canvas/caption classification, reagent-text subscript formatting, and
  bond-splitting, plus connector-internals coverage for the COM worker's
  timeout/nudge state machine and `targets.py`'s target-resolution and
  stale-cache-retry logic.
- `.venv\Scripts\python test_smoke_visual.py` — live smoke test; drives a
  visible ChemDraw window end to end. Watch it run.

## Development

Package layout, and the ChemDraw COM quirks this connector was built
around (COM crash traps, silent no-ops, timing/caching gotchas) — see
[`AGENTS.md`](AGENTS.md) whenever you're looking at the source, not only
if you're extending it. None of it matters if you're just talking to the
server through Claude (that's what the rest of this README covers), but
it's the fastest way to understand why the code is shaped the way it is
if you're reading it for any reason.

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
  and was relaunched; recover unsaved work from the automatic backups at
  `%LOCALAPPDATA%\chemdraw-mcp\backups`.
- Tools report structure ids like `claude-a1b2c3d4`; if one "no longer
  exists," the structure was deleted/rebuilt — ask Claude to call
  `chemdraw_get_document_state` to re-inventory the page.

## License

[MIT](LICENSE)
