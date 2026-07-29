"""Recover embedded ChemDraw structures from Office files (.pptx/.docx/
.xlsx) -- a chemist pastes a structure into a slide/manuscript/
spreadsheet, and these tools pull it back out later without needing
PowerPoint/Word/Excel open at all.

Two tools, split by cost:
- chemdraw_list_office_embeddings: offline, no ChemDraw needed. Just
  reports what's embedded and where -- a quick "is this file even worth
  extracting" check.
- chemdraw_extract_office_embeddings: the full pipeline. Needs ChemDraw
  open, since each embedding is a real .cdx blob that has to be converted
  to .cdxml the same way any other .cdx file would be
  (bridge.convert_cdx_cdxml), then parsed
  (chemdraw_connector.domain.cdxml_document.parse_document) for its
  structures.

Both call chemdraw_connector.domain.office_embed.find_embedded_cdx, which
does the actual zip/XML digging to locate embedded OLE objects inside the
Office file's package -- pure Python, no ChemDraw involvement, shared by
both tools here."""
import os
import shutil
import tempfile

from chemdraw_connector.domain import cdxml_document, office_embed

from ._common import as_json


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_list_office_embeddings(path: str):
        """Scan a .pptx/.docx/.xlsx file for embedded ChemDraw (OLE)
        objects and report what's there and where, WITHOUT converting or
        parsing any of it -- entirely offline, no live ChemDraw connection
        needed, works even if ChemDraw is closed. Use this first as a
        cheap "does this file even have anything worth extracting, and
        where" check before paying the cost of opening ChemDraw for the
        full chemdraw_extract_office_embeddings pipeline.

        path: path to the .pptx/.docx/.xlsx file to scan.

        Result shape:
        {
          "found": <int, total embeddings located>,
          "embeddings": [
            {
              "embedding_path": "ppt/embeddings/oleObject1.bin" (str,
                path inside the Office file's zip package -- useful for
                cross-referencing if you unzip the file by hand),
              "location": {...},
              "bytes": <int, size of the raw embedded .cdx data>
            },
            ...
          ]
        }

        `location.kind` tells you where in the document the embedding
        lives, and which other `location` fields are populated:
        - "slide" (.pptx only): `index` is the 1-based slide number the
          embedding appears on. `sheet_name`/`cell` are null.
        - "position" (.docx only): `index` is the embedding's body-order
          position in the document (not a page number -- Word doesn't
          expose page numbers for embedded objects this way).
          `sheet_name`/`cell` are null.
        - "sheet" (.xlsx only): `index` is null; `sheet_name` is the
          worksheet name and `cell` is the cell reference (e.g. "F10")
          the embedding is anchored to.

        This tool never raises for a single bad/corrupt embedding inside
        an otherwise-readable file -- that entry is just skipped and
        won't appear in the list. It DOES raise (as a normal tool error)
        if `path` itself isn't a readable .pptx/.docx/.xlsx file at all.

        Note: this only reports raw embedded .cdx byte counts -- it does
        NOT tell you whether an embedding actually contains recoverable
        chemistry data (a plain-Ctrl+V paste can produce a valid-looking
        but functionally empty embed). That determination only happens
        during chemdraw_extract_office_embeddings, which needs ChemDraw
        open to do the real conversion."""
        entries = office_embed.find_embedded_cdx(path)
        embeddings = [
            {
                "embedding_path": entry["embedding_path"],
                "location": entry["location"],
                "bytes": len(entry["cdx_bytes"]),
            }
            for entry in entries
        ]
        return as_json({"found": len(embeddings), "embeddings": embeddings})

    @mcp.tool()
    def chemdraw_extract_office_embeddings(path: str):
        """Recover the actual chemistry data from every ChemDraw object
        embedded in a .pptx/.docx/.xlsx file. Needs ChemDraw open -- each
        embedding is a real .cdx blob that this tool converts to .cdxml
        via a background ChemDraw document (same mechanism as any other
        .cdx -> .cdxml conversion) and then parses for structures. Run
        chemdraw_list_office_embeddings first if you just want to know
        what's there without paying this cost.

        path: path to the .pptx/.docx/.xlsx file to extract from.

        Result shape:
        {
          "found": <int, total embeddings located in the file>,
          "results": [
            {
              "embedding_path": ...,
              "location": ...,
              "structures": [...]   # from cdxml_document.parse_document's
                                      "structures" key
              # "note" present only for the empty-structures case, see below
            },
            ...
          ],
          "failed": [
            {"embedding_path": ..., "location": ..., "error": "..."},
            ...
          ]
        }

        Each embedding is processed independently -- one failing
        (corrupt data, a ChemDraw conversion error, an unparseable
        .cdxml, ...) is recorded in `failed` and does NOT abort the rest
        of the batch. This is the same per-item isolation convention used
        throughout this connector (e.g. chemdraw_make_reaction_route,
        chemdraw_edit_atoms) -- always check `failed` for anything
        non-empty rather than assuming every found embedding made it into
        `results`.

        IMPORTANT -- an empty `structures` list in a `results` entry is
        EXPECTED and NORMAL, not a bug or a failure: it lands in
        `results` (not `failed`) with a `"note"` field explaining why.
        This happens for a real, confirmed-live case -- a plain Ctrl+V
        paste of a ChemDraw structure into Office can create a
        syntactically valid embedded object that carries no live
        chemistry data at all, only a static preview picture that
        PowerPoint/Word/Excel displays separately from the embedding
        itself. Do not report these as errors to the user; the `note`
        field already explains it -- just pass it along."""
        entries = office_embed.find_embedded_cdx(path)
        results, failed = [], []
        tmpdir = tempfile.mkdtemp(prefix="chemdraw_office_embed_")
        try:
            for i, entry in enumerate(entries):
                embedding_path = entry["embedding_path"]
                location = entry["location"]
                try:
                    cdx_path = os.path.join(tmpdir, f"embed_{i}.cdx")
                    cdxml_path = os.path.join(tmpdir, f"embed_{i}.cdxml")
                    with open(cdx_path, "wb") as fh:
                        fh.write(entry["cdx_bytes"])

                    bridge.convert_cdx_cdxml(cdx_path, cdxml_path, overwrite=True)

                    with open(cdxml_path, "r", encoding="utf-8",
                              errors="replace") as fh:
                        text = fh.read()
                    parsed = cdxml_document.parse_document(text)
                    structures = parsed["structures"]

                    item = {
                        "embedding_path": embedding_path,
                        "location": location,
                        "structures": structures,
                    }
                    if not structures:
                        item["note"] = (
                            "no chemistry data recoverable -- this embedding "
                            "may be a paste-created placeholder with only a "
                            "static preview image, not a live ChemDraw "
                            "document"
                        )
                    results.append(item)
                except Exception as exc:
                    failed.append({
                        "embedding_path": embedding_path,
                        "location": location,
                        "error": str(exc),
                    })
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return as_json({
            "found": len(entries),
            "results": results,
            "failed": failed,
        })
