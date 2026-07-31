"""RDKit-based derivative enumeration (canvas only read, never written) and
generic CSV export."""
import csv
import os

from ..domain import enumeration
from ..errors import ChemDrawError, InvalidInputError, UnexpectedConnectorError


_SUPPORTED_FORMATS = ("smiles", "molfile")
_MAX_SUBSTITUENTS = 500


class _Enumeration:
    def enumerate_derivatives(self, substituents, scaffold=None, fmt="smiles",
                              properties=("mw", "formula")):
        if fmt not in _SUPPORTED_FORMATS:
            raise InvalidInputError(
                f"Unsupported format {fmt!r} for chemdraw_enumerate_derivatives. "
                f"Supported formats: {', '.join(_SUPPORTED_FORMATS)}."
            )
        if len(substituents) > _MAX_SUBSTITUENTS:
            raise InvalidInputError(
                f"Too many substituents ({len(substituents)}); "
                f"chemdraw_enumerate_derivatives supports at most "
                f"{_MAX_SUBSTITUENTS} per call."
            )
        # This method deliberately does NOT go through self._run — it
        # doesn't touch COM directly, and the export_structure call below
        # already submits its own closure to the single COM worker
        # thread; wrapping this whole method in another self._run too
        # would submit from INSIDE that worker thread while it's blocked
        # waiting on this same call, deadlocking ComWorker.submit (see
        # com/worker.py — one dedicated thread, no re-entrant submission).
        # So the RDKit/scaffold-resolution work below gets its own local
        # error boundary instead, mirroring _Plumbing._run's translation
        # (pass ChemDrawError subtypes through unchanged, wrap anything
        # else) without touching the worker thread.
        try:
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
        except ChemDrawError:
            raise
        except Exception as exc:
            raise UnexpectedConnectorError(exc) from exc
        return {
            "scaffold": scaffold,
            "derivatives": rows,
            "failed": failures,
            "count": len(rows),
        }

    def _write_csv_rows(self, rows, path, overwrite=False, create_parent=False):
        """Shared CSV-writing body for export_data_table and
        export_canvas_table — one on-disk format/convention (Excel-friendly
        BOM, header row = union of keys across all rows) for every tool
        that writes tabular results to a file.

        overwrite: guards against silently clobbering a pre-existing file
        at an LLM-constructed path, same motivation as
        _DocumentSession._guard_save_path/_Plumbing._guard_write_path.

        create_parent: forwarded to _guard_write_path -- False (default)
        for export_data_table, where `path` is always an explicit,
        LLM-constructed argument with no "connector's own default
        location" concept; export_canvas_table passes True only when
        writing to ITS OWN well-known default path. FIXED
        (DEBUG_REPORT.md M-4, 2026-07-31): this used to
        os.makedirs(exist_ok=True) unconditionally for every path
        (a plain existence check, not the shared _guard_write_path
        helper, on the mistaken belief that the default-path exemption
        required it -- it doesn't, that exemption is computed by the
        caller into the `overwrite` value before reaching here), silently
        creating a directory tree for a hallucinated custom path instead
        of the actionable error every sibling write guard gives."""
        if not rows:
            raise ValueError("No rows to write")
        self._guard_write_path(path, overwrite, create_parent=create_parent)
        fieldnames = list(dict.fromkeys(k for row in rows for k in row))
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return os.path.abspath(path)

    def export_data_table(self, rows, path, fmt="csv", overwrite=False):
        if fmt != "csv":
            raise ValueError("Only csv is supported")
        abspath = self._write_csv_rows(rows, path, overwrite=overwrite)
        return {"path": abspath, "rows": len(rows)}
