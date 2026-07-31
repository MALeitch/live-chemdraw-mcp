# ChemDraw COM type-library reference

This folder is a **live reflection dump** of ChemDraw's COM type library — the
full set of interfaces, methods, properties, and enums ChemDraw exposes over
COM automation — captured directly from a running ChemDraw instance rather
than inferred from documentation. It exists to answer one question quickly:
*"does ChemDraw's COM API support X, and what does that surface actually look
like?"* — without re-probing from scratch every time.

This complements, but doesn't duplicate, [`AGENTS.md`](../../AGENTS.md)'s
"Hard-won ChemDraw COM facts" section: that section documents facts about
code **already written** in this connector (landmines hit and fixed); this
folder is the source of truth for COM surface **confirmed to exist but not
yet implemented** here.

## Contents

- `probe_typelib.py` — dumps every interface/enum in the type library (230
  type infos as of the 2026-07-21 capture): every interface's method/property
  names, every enum's member names and values.
- `probe_typelib2.py` — a deep-dump script for a curated `TARGETS` set of
  specific interfaces/enums, printing full signatures (arg names, get/put
  pairs) and resolved enum values. `TARGETS` is meant to keep growing across
  probe sessions — add to it rather than rewriting it each time.
- `typelib_dump_<date>.txt` / `typelib_dump2_<date>.txt` — dated output of the
  two scripts above. Kept as separate dated snapshots (not merged into one
  evolving file) so past captures stay available for comparison across
  ChemDraw versions, matching this project's existing practice of preserving
  forensic/live-verified detail rather than summarizing it away.

## Provenance

First captured **2026-07-21** against **ChemDraw Professional 26.0.0.6141**
(the same build the main README's "Built and validated against" line
references), while investigating what COM capabilities exist but are unused
in this connector. See
`C:\Users\USER\.claude\plans\1-save-the-com-fuzzy-hopper.md` for the
resulting phased implementation plan (mechanism/reaction arrows, atom isotope
labeling, reaction symbols, polymer brackets, TLC plates, native
stoichiometry grids, enhanced-stereo flags, Markush/alt-groups).

## Regenerating

Requires a running, visible ChemDraw instance. Both scripts attach via
`win32com.client.GetActiveObject("ChemDraw_x64.Application")` —
**never `Dispatch()`**, which can launch a second ChemDraw.exe process
alongside whatever the user already has open (a documented landmine
elsewhere in this project — see the main README's contention notes).

```powershell
cd chemdraw-mcp
.venv\Scripts\python.exe docs\com_typelib\probe_typelib.py `
    > docs\com_typelib\typelib_dump_<date>.txt
.venv\Scripts\python.exe docs\com_typelib\probe_typelib2.py `
    > docs\com_typelib\typelib_dump2_<date>.txt
```

After a ChemDraw version bump, regenerate both and diff against the previous
dated files to see what changed in the object model.
