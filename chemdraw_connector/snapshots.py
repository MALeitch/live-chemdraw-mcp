"""Pre-batch document file backups (the real rollback point for multi-step
operations, since one logical action is many ChemDraw operations)."""
import datetime
import os
import re

BACKUP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "chemdraw-mcp", "backups",
)


def snapshot_document(doc):
    """Save a timestamped CDXML copy of doc. Returns the backup path, or None
    with a reason if the document can't be exported. Must run on the COM
    worker thread."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = getattr(doc, "name", "") or "untitled"
    safe = re.sub(r"[^\w.-]+", "_", name)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"{safe}-{stamp}.cdxml")
    data = doc.Objects.GetData("text/xml")
    if not data:
        return None
    text = data.tobytes().decode("utf-8", "replace") if isinstance(data, memoryview) else str(data)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
