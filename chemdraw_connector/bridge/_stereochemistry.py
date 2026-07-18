"""Reading and setting stereochemistry (CIP descriptors, wedge/hash bond
display)."""
from .. import targets
from ..com import types as t
from ._plumbing import SLOW_TIMEOUT


class _Stereochemistry:
    def get_stereochemistry(self, target="selection"):
        def go():
            doc = self._doc()
            out = []
            for u in targets.resolve(doc, target, self._cache_for(doc)):
                unit_atoms, unit_bonds = targets.unit_atoms_bonds(doc, u, self._cache_for(doc))
                atoms = []
                for i, a in enumerate(unit_atoms, start=1):
                    descriptor = t.ATOM_CIP_NAMES.get(int(a.Stereochemistry or 0))
                    if descriptor:
                        atoms.append({
                            "atom_index": i,
                            "element": t.element_symbol(a.ElementNumber),
                            "descriptor": descriptor,
                        })
                bonds = []
                for i, b in enumerate(unit_bonds, start=1):
                    descriptor = t.BOND_CIP_NAMES.get(int(b.Stereochemistry or 0))
                    display = t.bond_display_name(b.BondDisplay)
                    if descriptor or display != "plain":
                        bonds.append({
                            "bond_index": i,
                            "descriptor": descriptor,
                            "display": display,
                        })
                out.append({"id": targets.ensure_id(u), "atoms": atoms, "bonds": bonds})
            return {"stereochemistry": out}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def set_bond_stereo(self, target, bond_index, display):
        display_val = t.bond_display_value(display)

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            unit = targets.resolve(doc, target, cache)[0]
            bond, idx = targets.resolve_bond(doc, unit, bond_index, cache)
            bond.BondDisplay = display_val
            return {
                "id": targets.ensure_id(unit),
                "bond_index": idx,
                "ref": targets.bond_ref(bond),
                "display": t.bond_display_name(bond.BondDisplay),
                "note": "Verify the derived R/S with chemdraw_get_stereochemistry.",
            }
        return self._run(go, timeout=SLOW_TIMEOUT)
