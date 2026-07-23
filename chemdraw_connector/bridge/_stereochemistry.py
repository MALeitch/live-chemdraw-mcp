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

    def set_enhanced_stereo(self, target, atom_index, enhanced_type, group_number=0):
        """Set the "and1"/"or1" relative-stereo grouping notation on one
        atom -- used for reporting a single diastereomer out of a mixture.

        enhanced_type: unspecified | none | absolute | or | and.
        group_number: which "and"/"or" group this atom belongs to (e.g.
        group_number=1 for "and1"); ignored for "absolute"/"none".

        Found live directly on IChemDrawAtom (EnhancedStereoType +
        EnhancedStereoGroupNumber), confirmed settable — a per-atom
        property, not a document- or bond-level concept."""
        enhanced_val = t.enhanced_stereo_type_value(enhanced_type)

        def go():
            doc = self._doc()
            cache = self._cache_for(doc)
            unit = targets.resolve(doc, target, cache)[0]
            atom, idx = targets.resolve_atom(doc, unit, atom_index, cache)
            atom.EnhancedStereoType = enhanced_val
            atom.EnhancedStereoGroupNumber = group_number
            return {
                "id": targets.ensure_id(unit),
                "atom_index": idx,
                "ref": targets.atom_ref(atom),
                "enhanced_type": t.enhanced_stereo_type_name(atom.EnhancedStereoType),
                "group_number": atom.EnhancedStereoGroupNumber,
            }
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
