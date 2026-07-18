"""RDKit-based derivative enumeration (canvas only read, never written) and
generic CSV export."""
import csv
import os

from ..domain import enumeration
from ..errors import InvalidInputError


class _Enumeration:
    def enumerate_derivatives(self, substituents, scaffold=None, fmt="smiles",
                              properties=("mw", "formula")):
        if scaffold is None:
            exported = self.export_structure("smiles", "selection")["structures"]
            if not exported:
                raise InvalidInputError(
                    "No scaffold given and nothing selected in ChemDraw.")
            scaffold = exported[0]["data"]
        elif fmt == "molfile":
            from rdkit import Chem
            mol = Chem.MolFromMolBlock(scaffold)
            if mol is None:
                raise InvalidInputError("Scaffold molfile failed RDKit parsing")
            scaffold = Chem.MolToSmiles(mol)
        rows, failures = enumeration.enumerate_derivatives(
            scaffold, substituents, list(properties))
        return {
            "scaffold": scaffold,
            "derivatives": rows,
            "failed": failures,
            "count": len(rows),
        }

    @staticmethod
    def _write_csv_rows(rows, path):
        """Shared CSV-writing body for export_data_table and
        export_canvas_table — one on-disk format/convention (Excel-friendly
        BOM, header row = union of keys across all rows) for every tool
        that writes tabular results to a file."""
        if not rows:
            raise ValueError("No rows to write")
        fieldnames = list(dict.fromkeys(k for row in rows for k in row))
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return os.path.abspath(path)

    @staticmethod
    def export_data_table(rows, path, fmt="csv"):
        if fmt != "csv":
            raise ValueError("Only csv is supported")
        abspath = _Enumeration._write_csv_rows(rows, path)
        return {"path": abspath, "rows": len(rows)}
