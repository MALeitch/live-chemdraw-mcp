"""Target resolution. Every function here runs on the COM worker thread.

A "unit" is one addressable structure: a ChemDraw Group (what Data-inserts
create) or a top-level Fragment the user drew by hand that isn't inside any
group. Units are identified by a "claude_id" object tag; any untagged unit
gets tagged the first time it's observed, so hand-drawn content becomes
addressable too.
"""
import uuid

import pywintypes

from .errors import NothingSelectedError, TargetNotFoundError

TAG_NAME = "claude_id"


def new_id():
    return f"claude-{uuid.uuid4().hex[:8]}"


def get_id(unit):
    try:
        tag = unit.GetObjectTag(TAG_NAME)
    except Exception:
        return None
    if tag is None:
        return None
    try:
        return tag.StringValue or None
    except Exception:
        return None


def _stamp_id(unit, oid):
    tag = unit.MakeObjectTag(TAG_NAME, False)
    tag.StringValue = oid
    try:
        tag.Visible = False
        tag.Persistent = True
    except Exception:
        pass


def ensure_id(unit):
    oid = get_id(unit)
    if oid:
        return oid
    oid = new_id()
    _stamp_id(unit, oid)
    return oid


def retag(unit):
    """Force a fresh id onto `unit`, overwriting whatever it currently
    carries. Used by iter_units when a later group's tag collides with an
    earlier one already claimed this scan (see its docstring)."""
    oid = new_id()
    _stamp_id(unit, oid)
    return oid


CAPTION_OWNER_TAG = "claude_caption_owner"

# Sentinel tag_caption_owner value meaning "this caption intentionally has
# no single owning structure" -- e.g. a reaction scheme's reagents-text/
# "+"/arrow-fallback captions, which describe the WHOLE reaction, not any
# one reactant or product. Distinct from simply never being tagged: an
# untagged caption falls through to canvas.associate_captions' weaker
# spatial-proximity tiers, which is exactly what let a scheme's reagents
# caption get silently matched to the nearest structure (confirmed live:
# a "Pd, 80 degC" reagents caption sitting just above a product structure
# got reported as that product's OWN label) -- this sentinel makes tier 0
# resolve it to "no owner" directly instead, the same explicit-and-final
# answer decoration_ids already gives group-based decorations.
# Must be non-empty: get_caption_owner does `tag.StringValue or None`, so
# an empty-string sentinel would be indistinguishable from "never tagged".
# canvas.py can't import this directly (kept free of any COM/targets
# dependency by design, see its module docstring) -- its own copy of this
# exact literal in associate_captions must be kept in sync if this ever
# changes.
NO_CAPTION_OWNER = "__no_owner__"


def tag_caption_owner(cap, owner_id):
    """Stamp a Caption COM object with a persistent reference to the
    claude_id of the structure it was created for.

    Exists because `Caption.Group = unit` (the ChemDraw-native way to merge
    a caption into its structure's group) is a confirmed silent no-op on
    ChemDraw 26 (Professional 26.0.0.6141) — the assignment raises nothing,
    but reading `cap.Group` back afterward is always None. Without this,
    every caption this connector's own tools create (arrange_grid,
    build_scope_table, autonumber) would have no reliable link back to its
    structure at all, and canvas.associate_captions would have to fall
    through to its weaker spatial heuristics for every single one.

    Reuses the exact MakeObjectTag/StringValue mechanism already proven for
    structure units above (TAG_NAME/get_id/_stamp_id) rather than inventing
    a second identity scheme — live-verified (see test_bridge.py) that
    MakeObjectTag/GetObjectTag work the same way on a Caption object as on
    a Group/Fragment, and that the tag survives both moving the owning
    structure away and being read back through a freshly re-enumerated
    `doc.Captions.Item(i)` handle rather than the original object
    reference."""
    tag = cap.MakeObjectTag(CAPTION_OWNER_TAG, False)
    tag.StringValue = owner_id
    try:
        tag.Visible = False
        tag.Persistent = True
    except Exception:
        pass


def get_caption_owner(cap):
    """The claude_id of the structure `cap` was tagged for by
    tag_caption_owner, or None if it was never tagged this way (hand-drawn
    captions, captions from before this mechanism existed, or a caption
    this connector created before this fix landed)."""
    try:
        tag = cap.GetObjectTag(CAPTION_OWNER_TAG)
    except Exception:
        return None
    if tag is None:
        return None
    try:
        return tag.StringValue or None
    except Exception:
        return None


def doc_signature(doc):
    """Cheap O(1) fingerprint of a document's contents (three COM property
    reads, no scanning) used to tell whether a cached unit/atom/bond list
    is still valid — including catching hand edits the user made between
    tool calls, not just our own mutations."""
    return (doc.Groups.Count, doc.Atoms.Count, doc.Bonds.Count)


def unit_signature(unit):
    """Cheap O(1) fingerprint of one unit's contents, for atom/bond cache
    validation. Uses Objects.Atoms.Count/.Bonds.Count, the same
    unit-scoped-collection access already proven safe in
    state.describe_unit — only .Item() on unit-scoped collections crashes,
    not .Count."""
    objs = unit_objects(unit)
    return (objs.Atoms.Count, objs.Bonds.Count)


def iter_units(doc, cache=None):
    """All addressable structure units: groups first, then fragments that
    aren't already covered by a group.

    Atoms inside a Data-inserted structure report Group=None (their parent
    chain is atom -> fragment -> group), so every grouped structure would
    otherwise appear twice — the fragment inherits the group's tag, so
    dedupe by tag id. Untagged fragments (hand-drawn, never observed) have
    no id yet and are always included.

    Probed live on a real figure: copy-pasting a Data-inserted structure
    duplicates its `claude_id` tag verbatim (tags are Persistent) onto the
    pasted copy, so two independent top-level Groups end up answering to
    the same id — over half the "structures" in one document. Every
    id-keyed lookup (find_by_id, dict-keying by id) then silently resolves
    to only the first match; the second is invisible except via a
    document-wide sweep. A group whose tag collides with one already
    claimed this scan is retagged with a fresh id on the spot — first-seen
    (document order) keeps its original identity, so any reference a
    caller already holds for that id stays valid.

    The same copy-paste collision can happen to a top-level (ungrouped,
    hand-drawn) fragment too — copying one and pasting it without wrapping
    it in a Group duplicates its tag the same way. That case has to be
    told apart from the *expected* fragment/group tag collision (every
    grouped structure's own internal fragment inherits its owning group's
    tag, which is why the fragment loop below skips anything matching a
    tag already claimed by a Group — that fragment isn't a separate unit,
    it's the group's own innards). So a collision against a Group's id is
    always the benign, already-represented case and is skipped; a
    collision against another *fragment's* id seen earlier in this same
    scan is a genuine duplicate top-level structure and gets retagged,
    exactly like the Group case above — otherwise it would silently
    vanish from every id-keyed lookup, and its atoms/bonds would get
    folded into the original fragment's via unit_atoms_bonds' tag-based
    membership check.

    cache: optional dict (one per document, owned by the caller — see
    ChemDrawBridge._cache_for) used to skip this full scan when
    doc_signature(doc) still matches what was cached last time. Pass None
    (the default) to always scan fresh, e.g. from tests or callers with no
    cache to share.
    """
    if cache is not None:
        sig = doc_signature(doc)
        if cache.get("doc_sig") == sig and cache.get("units") is not None:
            return cache["units"]
    units = []
    group_ids = set()
    group_com_ids = set()
    for i in range(1, doc.Groups.Count + 1):
        grp = doc.Groups.Item(i)
        try:
            group_com_ids.add(grp.ID)
        except Exception:
            pass
        oid = get_id(grp)
        if oid and oid in group_ids:
            oid = retag(grp)
        if oid:
            group_ids.add(oid)
        units.append(grp)
    seen_fragment_ids = set()
    fragment_tag_ids = set()
    for i in range(1, doc.Atoms.Count + 1):
        atom = doc.Atoms.Item(i)
        try:
            frag = atom.Fragment
        except Exception:
            continue
        if frag is None:
            continue
        fid = frag.ID
        if fid in seen_fragment_ids:
            continue
        seen_fragment_ids.add(fid)
        if fid in group_com_ids:
            # atom.Fragment aliases the SAME underlying document object as
            # a Group already listed above -- confirmed live for a
            # just-contracted single-atom shorthand label (e.g.
            # ContractObjectsToLabel("Ph")): .Fragment on its one atom
            # dispatches as IChemDrawGroup with the identical .ID as the
            # Group already seen in doc.Groups, not a distinct Fragment
            # object. The tag-based check below (`oid in group_ids`)
            # can't catch this the first time it's observed, because
            # RIGHT AFTER a contraction BOTH sightings are still untagged
            # (get_id returns None for each, so "oid and oid in group_ids"
            # is false) -- they'd both get appended as two "units" for one
            # real structure. That un-deduped pair then gets cached
            # (doc_signature is just Groups/Atoms/Bonds counts, which
            # tagging doesn't change), so every later call sharing this
            # cache — not just this one — kept resolving the id to
            # whichever of the two aliased proxies happened to win in a
            # dict comprehension, and mutating through the "wrong" one is
            # what degraded the structure into an unusable zero-bounds
            # unit after arrange_in_region moved it. Comparing raw .ID
            # (doc-unique, unlike a tag that may not exist yet) catches
            # this at first sight instead of relying on a tag that isn't
            # there yet.
            continue
        oid = get_id(frag)
        if oid and oid in group_ids:
            continue  # this fragment lives inside a group we already listed
        if oid and oid in fragment_tag_ids:
            # A genuine duplicate top-level fragment (copy-pasted
            # hand-drawn content), not one merely inheriting an
            # already-listed group's tag — give it its own identity
            # instead of silently dropping it.
            oid = retag(frag)
        if oid:
            fragment_tag_ids.add(oid)
        units.append(frag)
    if cache is not None:
        cache["doc_sig"] = sig
        cache["units"] = units
        # The document changed (or this is the first scan): any cached
        # per-unit atom/bond lists (or formula strings) could now point at
        # stale membership too, e.g. after a contraction/expansion re-tags
        # a unit. Coarse but safe — rebuilt lazily, one unit at a time, on
        # next access.
        cache.setdefault("atom_bond", {}).clear()
        cache.setdefault("formula", {}).clear()
    return units


def unit_objects(unit):
    """The IChemDrawObjects collection scoped to one unit (for GetData,
    Formula, Move, ...). Groups expose .Objects directly; fragments may not."""
    objs = getattr(unit, "Objects", None)
    if objs is None:
        raise TargetNotFoundError(
            get_id(unit) or "(hand-drawn structure)"
        )
    return objs


def find_by_id(doc, object_id, cache=None):
    if not object_id:
        # get_id(unit) returns None for any never-tagged unit, so without
        # this guard `object_id=None` would compare equal to the first
        # untagged unit encountered (None == None) and silently "resolve"
        # to the wrong structure instead of raising.
        raise TargetNotFoundError(object_id)
    for unit in iter_units(doc, cache):
        if get_id(unit) == object_id:
            return unit
    raise TargetNotFoundError(object_id)


def unit_atoms_bonds(doc, unit, cache=None):
    """Doc-scoped Atom/Bond objects belonging to one unit, in document order.

    CRITICAL: never use unit-scoped collections' Item() (grp.Objects.Bonds
    .Item(i) etc.) — probed live, it hard-crashes the ChemDraw process.
    Document-scoped doc.Atoms/doc.Bonds Item access is stable, so membership
    is resolved through each atom/bond's Fragment, which inherits the unit's
    tag.

    cache: same per-document cache dict accepted by iter_units. Results are
    keyed by the unit's claude_id and unit_signature(unit), so repeated
    calls for a unit that hasn't changed skip the full doc.Atoms/doc.Bonds
    scan. Units without a usable .Objects (see unit_signature) are never
    cached, only scanned fresh every time."""
    uid = ensure_id(unit)
    if cache is not None:
        try:
            sig = unit_signature(unit)
        except Exception:
            sig = None
        if sig is not None:
            bucket = cache.setdefault("atom_bond", {})
            cached = bucket.get(uid)
            if cached is not None and cached[0] == sig:
                return cached[1], cached[2]
    atoms, bonds = [], []
    for coll, out in ((doc.Atoms, atoms), (doc.Bonds, bonds)):
        for i in range(1, coll.Count + 1):
            item = coll.Item(i)
            try:
                frag = item.Fragment
            except Exception:
                continue
            if frag is not None and get_id(frag) == uid:
                out.append(item)
    if cache is not None and sig is not None:
        cache.setdefault("atom_bond", {})[uid] = (sig, atoms, bonds)
    return atoms, bonds


def unit_formula(unit, cache=None):
    """objs.Formula, cached per (claude_id, unit_signature) the same way
    unit_atoms_bonds caches atom/bond lists — refetch only when the unit's
    own content changed, not on every read. Formula can't be derived from a
    CDXML export (a contracted nickname's true formula lives in ChemDraw's
    internal nickname database, not in the export — see
    domain/cdxml_snapshot.py), so this is the one property build_snapshot
    still reads live per unit, just no longer redundantly on every call."""
    uid = ensure_id(unit)
    try:
        sig = unit_signature(unit)
    except Exception:
        sig = None
    if cache is not None and sig is not None:
        bucket = cache.setdefault("formula", {})
        cached = bucket.get(uid)
        if cached is not None and cached[0] == sig:
            return cached[1]
    try:
        formula = unit_objects(unit).Formula or ""
    except Exception:
        formula = ""
    if cache is not None and sig is not None:
        cache.setdefault("formula", {})[uid] = (sig, formula)
    return formula


def atom_ref(atom):
    """Stable, session-scoped handle for one atom: ChemDraw's own doc-unique
    Atom.ID, already proven and in production use elsewhere (see
    bridge._build_handle_map; README confirms it equals the CDXML export
    node id). Cheaper and simpler than tagging every atom the way units
    are tagged — tagging is fine at unit granularity (a handful per
    document) but would add a COM round trip per atom at this scale."""
    return f"a{atom.ID}"


def bond_ref(bond):
    """Stable, session-scoped handle for one bond. Bond has no equivalent
    doc-unique .ID of its own (unverified/untrusted — see
    bridge._build_handle_map, which already avoids it), so this mirrors
    that exact proven pattern: the sorted pair of its atoms' ids."""
    a, b = bond.Atom1.ID, bond.Atom2.ID
    lo, hi = (a, b) if a <= b else (b, a)
    return f"b{lo}-{hi}"


def _as_legacy_index(ref):
    """A plain 1-based positional index, accepted alongside atom_ref/
    bond_ref strings for backward compatibility. Tolerates a numeric
    string too, since MCP tool args often arrive as JSON strings."""
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        s = ref.strip()
        if s.lstrip("-").isdigit():
            return int(s)
    return None


def _invalidate_cache(cache):
    """Force the next iter_units/unit_atoms_bonds call to do a fresh scan.

    Used when touching a cached atom/bond COM reference raises an error
    even though the cheap doc/unit signature check didn't flag anything —
    e.g. the user deletes one atom and draws a new one between two of
    Claude's calls, netting the same atom/bond count, so the count-based
    signature can't see it happened. See resolve_atom/resolve_bond."""
    if cache is None:
        return
    cache["doc_sig"] = None
    cache["units"] = None
    cache["atom_bond"] = {}
    cache["formula"] = {}


def _find_atom(atoms, ref, idx):
    if idx is not None:
        if not 1 <= idx <= len(atoms):
            raise ValueError(f"atom_index {idx} out of range 1..{len(atoms)}")
        atom = atoms[idx - 1]
        atom.ID  # touch it now, so a stale reference fails here, not later
        return atom, idx
    for i, atom in enumerate(atoms, start=1):
        if atom_ref(atom) == ref:
            return atom, i
    raise TargetNotFoundError(ref)


def _find_bond(bonds, ref, idx):
    if idx is not None:
        if not 1 <= idx <= len(bonds):
            raise ValueError(f"bond_index {idx} out of range 1..{len(bonds)}")
        bond = bonds[idx - 1]
        bond.Atom1.ID  # touch it now, so a stale reference fails here, not later
        return bond, idx
    for i, bond in enumerate(bonds, start=1):
        if bond_ref(bond) == ref:
            return bond, i
    raise TargetNotFoundError(ref)


def resolve_atom(doc, unit, ref, cache=None):
    """ref: a 1-based positional index (legacy — recomputed fresh each
    call, so it can shift as the structure changes) or an atom_ref()
    string (stable across calls; get one from chemdraw_list_atoms instead
    of guessing an index). Returns (atom, atom_index).

    A cached atom that turns out stale (a COM error when touched — the
    document changed underneath the cache in a way the cheap signature
    check couldn't see, see _invalidate_cache) triggers exactly one
    rebuild-and-retry, mirroring bridge._StaleHandleMap's precedent for
    the same class of problem elsewhere in this codebase. A second
    failure raises a clear, specific error instead of a raw COM
    exception."""
    idx = _as_legacy_index(ref)
    atoms, _ = unit_atoms_bonds(doc, unit, cache)
    try:
        return _find_atom(atoms, ref, idx)
    except pywintypes.com_error:
        if cache is None:
            raise
        _invalidate_cache(cache)
        atoms, _ = unit_atoms_bonds(doc, unit, cache)
        try:
            return _find_atom(atoms, ref, idx)
        except pywintypes.com_error as exc:
            raise TargetNotFoundError(
                f"{ref} (the document changed underneath this call and "
                "could not be resolved even after a fresh scan)"
            ) from exc


def resolve_bond(doc, unit, ref, cache=None):
    """Same contract as resolve_atom, for bonds/bond_ref(). Returns
    (bond, bond_index)."""
    idx = _as_legacy_index(ref)
    _, bonds = unit_atoms_bonds(doc, unit, cache)
    try:
        return _find_bond(bonds, ref, idx)
    except pywintypes.com_error:
        if cache is None:
            raise
        _invalidate_cache(cache)
        _, bonds = unit_atoms_bonds(doc, unit, cache)
        try:
            return _find_bond(bonds, ref, idx)
        except pywintypes.com_error as exc:
            raise TargetNotFoundError(
                f"{ref} (the document changed underneath this call and "
                "could not be resolved even after a fresh scan)"
            ) from exc


def _resolve_selection(doc, cache):
    sel = doc.Selection
    units, seen_ids = [], set()
    if sel is not None:
        try:
            for i in range(1, sel.Groups.Count + 1):
                grp = sel.Groups.Item(i)
                units.append(grp)
                seen_ids.add(ensure_id(grp))
        except Exception:
            pass
    # sel.Groups only ever reports Group-type selections. iter_units'
    # own definition of "unit" also includes a top-level Fragment the
    # user drew by hand and never wrapped in a Group (see module
    # docstring; README documents this as a real, observed case) --
    # sel.Groups has no equivalent for that. Rather than guess at an
    # unverified Selection.Fragments-style COM property, detect it the
    # same proven way bridge._capture_selection detects any selection
    # at all: a selected atom or bond. ensure_id (not get_id) keys the
    # dedup so a unit already added above, tagged or not, is never
    # added twice.
    for unit in iter_units(doc, cache):
        oid = ensure_id(unit)
        if oid in seen_ids:
            continue
        atoms, bonds = unit_atoms_bonds(doc, unit, cache)
        if any(a.Selected for a in atoms) or any(b.Selected for b in bonds):
            units.append(unit)
            seen_ids.add(oid)
    if not units:
        raise NothingSelectedError()
    return units


ANNOTATION_COLLECTIONS = {
    "arrow": "Arrows",
    "symbol": "Symbols",
    "bracket": "Brackets",
    "tlc_plate": "TLCPlates",
    "caption": "Captions",
}


def iter_annotations(doc, kind):
    """Free-floating annotation objects (Arrow, Symbol, ...) live in their
    own document-level collection (doc.Arrows, doc.Symbols, ...), confirmed
    live to exist alongside doc.Groups/doc.Captions/doc.Graphics rather than
    being reachable through iter_units — an Arrow/Symbol is not a chemical
    structure and must never show up in target="document"/"selection"
    structure resolution (chemdraw_describe_canvas/chemdraw_move_objects
    both assume that shape). Deliberately parallel to iter_units, not a
    change to it. No caching here (unlike iter_units): annotation counts per
    document are small and these are looked up far less often than
    structure units, so a fresh scan each time isn't worth the complexity a
    signature-keyed cache would add."""
    coll = getattr(doc, ANNOTATION_COLLECTIONS[kind])
    return [coll.Item(i) for i in range(1, coll.Count + 1)]


def find_annotation_by_id(doc, kind, object_id):
    """Same contract as find_by_id, scoped to one annotation kind
    ("arrow"/"symbol"). Tagging reuses ensure_id/get_id unchanged — proven
    live to work on non-Group objects already (see tag_caption_owner above);
    only the enumeration differs from a structure unit."""
    if not object_id:
        raise TargetNotFoundError(object_id)
    for obj in iter_annotations(doc, kind):
        if get_id(obj) == object_id:
            return obj
    raise TargetNotFoundError(object_id)


def find_removable_by_id(doc, object_id, cache=None):
    """Find ANY addressable object by id for deletion purposes — a
    structure unit first (the common case), then every annotation kind
    (caption, arrow, symbol, bracket, tlc_plate) in turn.

    Exists because a caption or a bad TLC plate/bracket/arrow used to be
    completely unrecoverable once created: chemdraw_remove's target
    resolution (targets.resolve/find_by_id) only ever searched
    iter_units — real chemical structures — so an orphaned caption (e.g.
    left behind after its owning structure was deleted, or a caption
    that got put in the wrong place) or a mis-built annotation had no id
    space it could be addressed through at all, and neither
    chemdraw_remove nor chemdraw_undo (which doesn't cover this
    connector's own mutations anyway — see bridge.undo) could reach it.
    Every one of these object kinds is confirmed live to expose a plain
    .Delete() method (Caption, Arrow, TLCPlate all confirmed directly;
    Symbol/Bracket share the same base object interface) — see
    bridge._Manipulation.remove for how this return value is used.

    Returns (kind, obj) where kind is "structure" or one of
    ANNOTATION_COLLECTIONS' keys. Raises TargetNotFoundError if nothing
    matches anywhere."""
    try:
        return "structure", find_by_id(doc, object_id, cache)
    except TargetNotFoundError:
        pass
    for kind in ANNOTATION_COLLECTIONS:
        try:
            return kind, find_annotation_by_id(doc, kind, object_id)
        except TargetNotFoundError:
            continue
    raise TargetNotFoundError(object_id)


def resolve(doc, target, cache=None):
    """Resolve a target spec to a list of units.

    target: "selection" | "document" | object_id | list of object_ids
    """
    if target == "document":
        return [u for u in iter_units(doc, cache)]
    if target == "selection":
        # Touching a cached atom/bond's .Selected below can raise the same
        # way resolve_atom/resolve_bond's touch can: a cached unit
        # unrelated to this call's own selection may have been edited by
        # hand (or by another tool call) since it was cached, netting the
        # same atom/bond count so the cheap signature check missed it (see
        # _invalidate_cache). Without this retry, that stale reference
        # would raise a raw COM error out of `resolve` every time
        # target="selection" is used — the default for most editing tools
        # — until that unrelated unit's count happened to change again.
        # Mirrors resolve_atom/resolve_bond's one-retry contract exactly.
        try:
            return _resolve_selection(doc, cache)
        except pywintypes.com_error:
            if cache is None:
                raise
            _invalidate_cache(cache)
            try:
                return _resolve_selection(doc, cache)
            except pywintypes.com_error as exc:
                raise TargetNotFoundError(
                    "selection (the document changed underneath this call "
                    "and could not be resolved even after a fresh scan)"
                ) from exc
    if isinstance(target, str):
        return [find_by_id(doc, target, cache)]
    if isinstance(target, (list, tuple)):
        return [find_by_id(doc, oid, cache) for oid in target]
    raise ValueError(f"Unintelligible target: {target!r}")
