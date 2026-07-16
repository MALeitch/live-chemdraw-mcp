"""Pre-batch document file backups (the real rollback point for multi-step
operations, since one logical action is many ChemDraw operations)."""
import datetime
import os
import re
import threading
import uuid

BACKUP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "chemdraw-mcp", "backups",
)


def export_cdxml_text(doc):
    """The COM half of a backup: doc.Objects.GetData("text/xml"). Must run
    on the COM worker thread. Returns decoded text, or None if the document
    can't be exported."""
    data = doc.Objects.GetData("text/xml")
    if not data:
        return None
    return (data.tobytes().decode("utf-8", "replace")
            if isinstance(data, memoryview) else str(data))


def write_backup_file(doc_name, text, wait=True):
    """The disk half of a backup: pure Python I/O, no COM involved, so it's
    safe to run off the COM worker thread. Returns the backup path.

    With wait=False the write happens on a background thread and the path
    is returned before the write completes — so a slow disk never blocks
    the single COM worker thread that produced `text`. The path is still
    deterministic (computed before spawning the thread), so callers can
    hand it back immediately."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", doc_name or "untitled")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    # Second-resolution timestamps alone collide: two distinct
    # backup-triggering mutations within the same wall-clock second would
    # silently overwrite each other's rollback file. A short uuid4 suffix
    # (same style as targets.new_id) guarantees uniqueness regardless of
    # clock resolution, while the timestamp prefix keeps the directory
    # sorted/readable for a human browsing backups.
    unique = uuid.uuid4().hex[:6]
    path = os.path.join(BACKUP_DIR, f"{safe}-{stamp}-{unique}.cdxml")

    def do_write():
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    if wait:
        do_write()
    else:
        threading.Thread(target=do_write, daemon=True).start()
    return path


def snapshot_document(doc):
    """Save a timestamped CDXML copy of doc synchronously. Returns the
    backup path, or None if the document can't be exported. Must run on
    the COM worker thread. ChemDrawBridge's mutating tools use the
    debounced, off-thread-write path instead — see bridge._maybe_snapshot."""
    text = export_cdxml_text(doc)
    if text is None:
        return None
    return write_backup_file(getattr(doc, "name", "") or "untitled", text)
