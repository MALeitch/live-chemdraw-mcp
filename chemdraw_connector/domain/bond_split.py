"""Splitting a molecular graph at one bond into two sides.

Zero COM imports; pure graph traversal over plain atom/bond ids, so it's
unit-testable without ChemDraw. Feeds bridge.split_at_bond, which is how
Claude picks "which atoms to fold/flip" before calling chemdraw_transform
on an arbitrary sub-selection instead of a whole structure.
"""


class RingBondSplitError(ValueError):
    """The chosen bond is part of a ring/cycle — there is no single 'side'
    to split off, since an alternate path connects its two endpoints even
    with this bond removed."""


def split_atoms(atom_ids, bonds, bond_atom1_id, bond_atom2_id, side_atom_id):
    """Split a structure at one bond into the side containing side_atom_id.

    atom_ids: iterable of atom ids (hashable).
    bonds: iterable of (bond_token, atom1_id, atom2_id). bond_token is an
        opaque passthrough — never inspected here beyond identity/equality
        — so the caller can pass a live COM Bond object directly and get
        it back unchanged in the result.
    bond_atom1_id, bond_atom2_id: the two endpoints of the bond to split
        at. Must both appear in atom_ids and be connected by an entry in
        `bonds` (order-independent) — a plain ValueError otherwise.
    side_atom_id: any atom id on the side to select; need not be one of
        the bond's own endpoints (e.g. it can be the far tip of a
        branch) — BFS from here reaches the whole side regardless. Must
        appear in atom_ids.

    Returns {"atom_ids": frozenset(reachable), "bond_ids": [...]}
    — bond_ids are every bond (from `bonds`) with BOTH endpoints in the
    reachable set, i.e. everything that moves rigidly with the selection.
    A plain list, not a frozenset: bond_token is opaque and callers may
    pass live COM Bond objects, which aren't hashable.
    The split bond itself is never included: exactly one of its endpoints
    is reachable, never both, once the ring case below is ruled out.

    Raises RingBondSplitError if the split bond turns out to be part of a
    ring/cycle: with that one edge removed, its two endpoints are STILL
    mutually reachable via some other path, so there is no coherent
    "one side" of it.
    """
    atom_id_set = set(atom_ids)
    if side_atom_id not in atom_id_set:
        raise ValueError(f"side_atom_id {side_atom_id!r} is not in atom_ids")
    if bond_atom1_id not in atom_id_set:
        raise ValueError(f"bond_atom1_id {bond_atom1_id!r} is not in atom_ids")
    if bond_atom2_id not in atom_id_set:
        raise ValueError(f"bond_atom2_id {bond_atom2_id!r} is not in atom_ids")

    bonds = list(bonds)
    split_pair = frozenset((bond_atom1_id, bond_atom2_id))
    if not any(frozenset((a, b)) == split_pair for _, a, b in bonds):
        raise ValueError(
            f"atoms {bond_atom1_id!r} and {bond_atom2_id!r} are not connected "
            "by any bond in `bonds`"
        )

    adjacency = {}
    for _, a, b in bonds:
        if frozenset((a, b)) == split_pair:
            continue  # this is the edge being split at — excluded from BFS
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    visited = {side_atom_id}
    frontier = [side_atom_id]
    while frontier:
        current = frontier.pop()
        for neighbor in adjacency.get(current, ()):
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append(neighbor)

    if bond_atom1_id in visited and bond_atom2_id in visited:
        raise RingBondSplitError(
            f"bond {bond_atom1_id!r}-{bond_atom2_id!r} is part of a ring: "
            "an alternate path connects its two endpoints even with this "
            "bond removed, so there is no single 'side' to select."
        )

    bond_ids = [
        token for token, a, b in bonds if a in visited and b in visited
    ]
    return {"atom_ids": frozenset(visited), "bond_ids": bond_ids}
