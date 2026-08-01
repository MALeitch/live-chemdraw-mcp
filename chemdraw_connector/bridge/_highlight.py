"""Reproduces ChemDraw's own "Highlight Color" GUI tool -- see
domain/highlight_cdxml.py's module docstring for the full investigation
(no matching COM-settable property exists; the real mechanism is a
`highlightColor` CDXML attribute). Since there's no live property to set,
this works by export -> inject -> delete -> reimport: the same
round-trip shape as every other CDXML-text-based insertion in this
codebase (bridge/_plumbing.py's _insert_raw), just applied to an EXISTING
structure instead of a brand-new one."""
from .. import snapshots, targets
from ..domain import highlight_cdxml as hc
from ._plumbing import SLOW_TIMEOUT, _com_text
from ..errors import ChemDrawError, InvalidInputError


def _parse_atom_ref(ref):
    if not ref.startswith("a"):
        raise InvalidInputError(f"Not an atom ref (expected 'aN'): {ref!r}")
    return int(ref[1:])


def _parse_bond_ref(ref):
    if not ref.startswith("b") or "-" not in ref:
        raise InvalidInputError(f"Not a bond ref (expected 'bN-M'): {ref!r}")
    lo, hi = ref[1:].split("-", 1)
    return (int(lo), int(hi))


class _Highlight:
    def highlight_structure(self, target, highlights):
        """Apply (or clear) MULTIPLE highlight regions/colors on `target`
        (a single structure -- unlike most tools here, this can't operate
        on "document"/"selection"/a multi-id list, since it deletes and
        reinserts the ONE structure it touches) IN ONE export -> inject ->
        reimport round trip -- e.g. a 6-color "rainbow" highlight across
        one molecule is ONE call with 6 entries, not 6 calls. There is no
        technical reason to split this across multiple calls: the
        underlying domain.highlight_cdxml.set_highlight/clear_highlight
        can already be invoked repeatedly against the SAME parsed CDXML
        tree before it's ever serialized/reimported, so batching every
        requested region into one pass before the one delete+reimport is
        strictly better than one round trip per region -- fewer COM
        round trips, and no atom_refs/bond_refs staleness between calls
        (see below) to worry about, since every ref in `highlights` is
        resolved against the SAME pre-mutation export.

        highlights: list of {"color": "#RRGGBB" or None (clear that
        region), "atom_refs": [...]?, "bond_refs": [...]?}. Omitting
        both refs in an entry means "the whole structure" for that
        entry's color -- matching "select the ring, hit Apply" in the
        GUI. Entries are applied in order; a later entry's region can
        overlap and override an earlier one's color (last write wins,
        same as calling the GUI tool twice on overlapping selections).

        Necessarily replaces the structure's underlying ChemDraw objects
        (delete + reimport of modified CDXML -- there is no live property
        to set). CONFIRMED LIVE: the returned `id` is the SAME as
        `target`, not a new one -- the exported CDXML carries the
        original claude_id ObjectTag along with it (untouched by the
        highlight injection), so the reimported object's tag survives
        the round trip and `ensure_id` finds and reuses it rather than
        minting a fresh one. Position is explicitly restored to match
        the original after reimport -- CONFIRMED LIVE this is necessary,
        not a formality: a per-unit CDXML export/reimport round trip
        does NOT reliably land back at the same on-page coordinates it
        was exported from, so skipping this step can leave the
        highlighted copy overlapping unrelated structures elsewhere on
        the page. atom_refs/bond_refs from BEFORE this call become stale
        AFTER it (reimport reassigns fresh internal atom/bond ids) -- if
        you need to highlight the same structure again later, call
        chemdraw_list_atoms again first; within a SINGLE call here, refs
        for every entry in `highlights` are still valid since they all
        resolve against the one pre-mutation export."""
        if target in ("document", "selection") or isinstance(target, (list, tuple)):
            raise InvalidInputError(
                f"highlight_structure needs a single structure id, not "
                f"{target!r} -- 'document'/'selection'/a list can resolve "
                "to more than one structure, and this tool deletes and "
                "reimports exactly the one it touches. Resolve the id(s) "
                "first (e.g. via chemdraw_get_document_state) and call "
                "this once per structure."
            )
        if not highlights:
            raise InvalidInputError(
                "highlights is empty -- pass at least one "
                "{\"color\": ..., \"atom_refs\"?, \"bond_refs\"?} entry."
            )
        parsed_groups = []
        for h in highlights:
            atom_refs = h.get("atom_refs")
            bond_refs = h.get("bond_refs")
            if not atom_refs and not bond_refs:
                # BOTH omitted -- the documented "whole structure" case.
                # None/None tells the domain layer "match everything" (see
                # set_highlight/clear_highlight's want_all_atoms/
                # want_all_bonds).
                atom_ids, bond_pairs = None, None
            else:
                # CONFIRMED LIVE this distinction matters, not pedantic:
                # an entry that gives atom_refs but omits bond_refs means
                # "just these atoms, no bonds" -- treating the omitted
                # side as None (= "match everything") here made every
                # atom-only entry ALSO recolor all 20 bonds, and every
                # bond-only entry ALSO recolor all 19 atoms, so in a
                # multi-entry rainbow call each later entry silently wiped
                # out the previous entries' atom (or bond) colors. `[]`
                # (not None) for the omitted side means "match nothing",
                # which is what a PARTIAL entry actually intends.
                atom_ids = [_parse_atom_ref(r) for r in atom_refs] if atom_refs else []
                bond_pairs = [_parse_bond_ref(r) for r in bond_refs] if bond_refs else []
            parsed_groups.append((h.get("color"), atom_ids, bond_pairs))

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            unit = targets.resolve(doc, target, cache)[0]
            objs = targets.unit_objects(unit)
            # Read BEFORE Clear() -- the object (and its Left/Top) won't
            # exist to read afterward. Used below to restore position
            # after reimport, since reimport does not preserve it (see
            # docstring).
            orig_left, orig_top = unit.Left, unit.Top
            raw_cdxml = _com_text(objs.GetData("text/xml"))
            if not raw_cdxml:
                raise ChemDrawError(
                    "ChemDraw returned no CDXML for this structure -- cannot "
                    "highlight it."
                )
            root = hc.parse(raw_cdxml)
            applied = []
            for color, atom_ids, bond_pairs in parsed_groups:
                if color is not None:
                    index = hc.resolve_color_index(root, color)
                    a_count, b_count = hc.set_highlight(
                        root, index, atom_ids, bond_pairs)
                else:
                    a_count, b_count = hc.clear_highlight(
                        root, atom_ids, bond_pairs)
                applied.append({
                    "color": color,
                    "highlighted_atoms": a_count,
                    "highlighted_bonds": b_count,
                })
            modified = hc.serialize(root)

            backup = self._maybe_snapshot(doc)
            atoms_before = doc.Atoms.Count
            objs.Clear()
            # Counted AFTER Clear(), not before -- CONFIRMED LIVE this
            # matters, not cosmetic: counting before Clear() and diffing
            # against the post-reimport count nets to ~0 apparent change
            # (delete -1, insert +1), so "no new structure found" fires
            # even when the reimport genuinely succeeded.
            groups_before = doc.Groups.Count
            self._insert_raw(doc.Objects, "text/xml", modified)
            # Invalidate the cache UNCONDITIONALLY right after mutating,
            # before any validation/error-raising below -- CONFIRMED LIVE
            # that skipping this on an error path leaves the cache
            # holding pre-mutation unit references, which then silently
            # corrupts EVERY SUBSEQUENT call in the same cached session:
            # a completely different, never-touched structure got
            # misidentified as this call's target on a later call,
            # because the stale cache mapped its id to the wrong live
            # COM object. Same "invalidate before anything else can see
            # stale state" precedent as _insert_structure_units.
            targets._invalidate_cache(cache)
            if doc.Atoms.Count != atoms_before:
                raise ChemDrawError(
                    f"Reimport after highlighting produced {doc.Atoms.Count} "
                    f"atoms, expected {atoms_before} (the original count) -- "
                    "the modified CDXML may not have round-tripped cleanly. "
                    f"A pre-mutation backup is at {backup}."
                )
            new_units = [
                doc.Groups.Item(i)
                for i in range(groups_before + 1, doc.Groups.Count + 1)
            ]
            if len(new_units) != 1:
                raise ChemDrawError(
                    f"Reimport after highlighting produced {len(new_units)} "
                    "structures, expected 1 -- the modified CDXML may have "
                    f"split. A pre-mutation backup is at {backup}."
                )
            new_unit = new_units[0]
            new_objs = targets.unit_objects(new_unit)
            dx, dy = orig_left - new_unit.Left, orig_top - new_unit.Top
            if dx or dy:
                new_objs.Move(dx, dy)
            return {
                "id": targets.ensure_id(new_unit),
                "applied": applied,
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)
