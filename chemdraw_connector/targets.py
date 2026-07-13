"""Target resolution. Every function here runs on the COM worker thread.

A "unit" is one addressable structure: a ChemDraw Group (what Data-inserts
create) or a top-level Fragment the user drew by hand that isn't inside any
group. Units are identified by a "claude_id" object tag; any untagged unit
gets tagged the first time it's observed, so hand-drawn content becomes
addressable too.
"""
import uuid

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


def ensure_id(unit):
    oid = get_id(unit)
    if oid:
        return oid
    oid = new_id()
    tag = unit.MakeObjectTag(TAG_NAME, False)
    tag.StringValue = oid
    try:
        tag.Visible = False
        tag.Persistent = True
    except Exception:
        pass
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
        units.append(grp)
        oid = get_id(grp)
        if oid:
            seen_ids.add(oid)
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
        # per-unit atom/bond lists could now point at stale membership too,
        # e.g. after a contraction/expansion re-tags a unit. Coarse but
        # safe — rebuilt lazily, one unit at a time, on next access.
        cache.setdefault("atom_bond", {}).clear()
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
