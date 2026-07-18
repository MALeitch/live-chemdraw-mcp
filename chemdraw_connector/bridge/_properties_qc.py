"""Properties, IUPAC naming, HRMS characterization text, duplicate
detection, and chemical-warning QC."""
from .. import targets
from ..com import types as t
from ..domain import characterization, dedup
from ._plumbing import SLOW_TIMEOUT, _com_text


class _PropertiesQC:
    def get_properties(self, target="selection"):
        def go():
            doc = self._doc()
            out = []
            for u in targets.resolve(doc, target, self._cache_for(doc)):
                objs = targets.unit_objects(u)
                out.append({
                    "id": targets.ensure_id(u),
                    "formula": objs.Formula or "",
                    "molecular_weight": round(objs.MolecularWeight, 4),
                    "exact_mass": round(objs.ExactMass, 4),
                })
            return {"properties": out}
        return self._run(go)

    def get_iupac_name(self, target="selection", html=False):
        fmt = "htmlname" if html else "name"

        def go():
            doc = self._doc()
            out = []
            for u in targets.resolve(doc, target, self._cache_for(doc)):
                data = targets.unit_objects(u).GetData(t.mime_for(fmt))
                out.append({"id": targets.ensure_id(u), "name": _com_text(data)})
            return {"names": out}
        return self._run(go, timeout=SLOW_TIMEOUT)

    def generate_characterization_text(self, target="selection",
                                       ion_mode="[M+H]+", technique="ESI"):
        props = self.get_properties(target)["properties"]
        lines = [
            {
                "id": p["id"],
                "formula": p["formula"],
                "text": characterization.hrms_line(
                    p["formula"], p["exact_mass"], ion_mode, technique
                ),
            }
            for p in props
        ]
        return {"characterization": lines}

    def find_duplicates(self, target="document"):
        exported = self.export_structure("inchikey", target)["structures"]
        keyed = [(e["id"], e["data"]) for e in exported]
        return dedup.find_duplicate_groups(keyed)

    def check_warnings(self, target="document"):
        def go():
            doc = self._doc()
            warnings = []
            if target == "document":
                collections = [
                    ([doc.Atoms.Item(i) for i in range(1, doc.Atoms.Count + 1)], "atom"),
                    ([doc.Bonds.Item(i) for i in range(1, doc.Bonds.Count + 1)], "bond"),
                ]
            else:
                collections = []
                for u in targets.resolve(doc, target, self._cache_for(doc)):
                    atoms, bonds = targets.unit_atoms_bonds(doc, u, self._cache_for(doc))
                    collections.extend([(atoms, "atom"), (bonds, "bond")])
            for coll, kind in collections:
                for i, item in enumerate(coll, start=1):
                    msg = item.ChemicalWarning
                    if msg:
                        entry = {"kind": kind, "index": i, "warning": msg}
                        if kind == "atom":
                            entry["element"] = t.element_symbol(item.ElementNumber)
                        warnings.append(entry)
            return {
                "total_document_warnings": doc.NumChemicalWarnings,
                "flagged": warnings,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)
