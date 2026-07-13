"""Backup-snapshot debounce logic — pure, no COM, no live ChemDraw.

ChemDrawBridge() itself is safe to construct here: Connection/ComWorker are
both lazy and only touch COM/spawn threads once a tool call actually runs
(see com/connection.py's Connection.app(), com/worker.py's _ensure_thread).
_maybe_snapshot takes `doc` as a plain argument, so a fake doc exercises it
without any of that."""
import os
import time

from chemdraw_connector import snapshots
from chemdraw_connector.bridge import ChemDrawBridge


class _Count:
    def __init__(self, n):
        self.Count = n


class FakeObjects:
    def __init__(self, text="<CDXML/>"):
        self._text = text

    def GetData(self, mime):
        assert mime == "text/xml"
        return self._text


class FakeDoc:
    def __init__(self, name="doc1", groups=0, atoms=0, bonds=0, text="<CDXML/>"):
        self.name = name
        self.Groups = _Count(groups)
        self.Atoms = _Count(atoms)
        self.Bonds = _Count(bonds)
        self.Objects = FakeObjects(text)


def test_maybe_snapshot_reuses_path_when_signature_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "BACKUP_DIR", str(tmp_path))
    b = ChemDrawBridge()
    doc = FakeDoc(atoms=2)
    path1 = b._maybe_snapshot(doc)
    path2 = b._maybe_snapshot(doc)
    assert path1 == path2, "backup should be reused when nothing changed since the last one"


def test_maybe_snapshot_takes_fresh_backup_after_signature_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "BACKUP_DIR", str(tmp_path))
    b = ChemDrawBridge()
    doc = FakeDoc(atoms=2)
    path1 = b._maybe_snapshot(doc)
    doc.Atoms.Count = 3  # simulate a mutation changing the document
    time.sleep(1.01)  # backup filenames are second-resolution timestamps
    path2 = b._maybe_snapshot(doc)
    assert path2 != path1, "a doc-level change must trigger a fresh backup, not a reused path"


def test_maybe_snapshot_write_is_off_thread_but_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "BACKUP_DIR", str(tmp_path))
    b = ChemDrawBridge()
    doc = FakeDoc(atoms=2, text="<CDXML>hello</CDXML>")
    path = b._maybe_snapshot(doc)
    assert path
    for _ in range(20):
        if os.path.exists(path):
            break
        time.sleep(0.05)
    assert os.path.exists(path), "backup file should land shortly after the call returns"
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == "<CDXML>hello</CDXML>"
