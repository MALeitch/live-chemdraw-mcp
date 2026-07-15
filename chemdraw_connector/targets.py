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
    seen_ids = set()
    for i in range(1, doc.Groups.Count + 1):
        grp = doc.Groups.Item(i)
        oid = get_id(grp)
        if oid and oid in seen_ids:
            oid = retag(grp)
        if oid:
            seen_ids.add(oid)
        units.append(grp)
    seen_fragment_ids = set()
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
        oid = get_id(frag)
        if oid and oid in seen_ids:
            continue  # this fragment lives inside a group we already listed
        if oid:
            seen_ids.add(oid)  # sibling fragments can share an inherited tag
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


def resolve(doc, target, cache=None):
    """Resolve a target spec to a list of units.

    target: "selection" | "document" | object_id | list of object_ids
    """
    if target == "document":
        return [u for u in iter_units(doc, cache)]
    if target == "selection":
        sel = doc.Selection
        units = []
        if sel is not None:
            try:
                for i in range(1, sel.Groups.Count + 1):
                    units.append(sel.Groups.Item(i))
            except Exception:
                pass
        if not units:
            raise NothingSelectedError()
        return units
    if isinstance(target, str):
        return [find_by_id(doc, target, cache)]
    if isinstance(target, (list, tuple)):
        return [find_by_id(doc, oid, cache) for oid in target]
    raise ValueError(f"Unintelligible target: {target!r}")
