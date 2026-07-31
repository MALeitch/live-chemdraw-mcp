"""_write_csv_rows / export_data_table -- pure, no COM.

Regression test. _write_csv_rows used to os.makedirs(exist_ok=True)
unconditionally for every path, including an explicit/LLM-constructed
one -- silently
materializing a directory tree instead of the actionable
"containing directory does not exist" error every sibling write guard
(_guard_write_path/_guard_save_path) gives. Now routes through the shared
_guard_write_path, with a create_parent opt-in used ONLY by
export_canvas_table's own well-known default location (tested at the
_layout.py call-site level is impractical without full COM mocking, so
this covers _write_csv_rows/export_data_table directly plus the
create_parent flag itself).
"""
import os

import pytest

from chemdraw_connector.bridge._enumeration import _Enumeration
from chemdraw_connector.bridge._plumbing import _Plumbing
from chemdraw_connector.errors import InvalidInputError


class _StubBridge(_Plumbing, _Enumeration):
    def __init__(self):
        pass  # skip _Plumbing.__init__ -- never touches COM in these tests


def test_export_data_table_refuses_missing_parent_directory_not_creates_it(tmp_path):
    bridge = _StubBridge()
    missing_dir = tmp_path / "does" / "not" / "exist"
    target = missing_dir / "out.csv"

    with pytest.raises(InvalidInputError, match="does not exist"):
        bridge.export_data_table([{"a": 1}], str(target))

    assert not missing_dir.exists()  # must NOT have been silently created


def test_export_data_table_writes_when_directory_exists(tmp_path):
    bridge = _StubBridge()
    target = tmp_path / "out.csv"

    result = bridge.export_data_table([{"a": 1, "b": 2}], str(target))

    assert result["rows"] == 1
    assert os.path.exists(target)


def test_export_data_table_refuses_overwrite_by_default(tmp_path):
    bridge = _StubBridge()
    target = tmp_path / "out.csv"
    target.write_text("existing")

    with pytest.raises(InvalidInputError, match="already exists"):
        bridge.export_data_table([{"a": 1}], str(target))
    assert target.read_text() == "existing"  # untouched


def test_write_csv_rows_create_parent_true_creates_missing_directory(tmp_path):
    # This is the ONLY sanctioned use of create_parent=True --
    # export_canvas_table's own well-known default location, which this
    # connector owns and controls, same discipline as
    # snapshots.write_backup_file's own os.makedirs(exist_ok=True).
    bridge = _StubBridge()
    missing_dir = tmp_path / "chemdraw-mcp"
    target = missing_dir / "chemdraw-mcp-canvas-export.csv"

    result = bridge._write_csv_rows([{"a": 1}], str(target), overwrite=True,
                                    create_parent=True)

    assert os.path.exists(result)
    assert missing_dir.is_dir()


def test_write_csv_rows_create_parent_false_still_refuses(tmp_path):
    bridge = _StubBridge()
    missing_dir = tmp_path / "custom"
    target = missing_dir / "out.csv"

    with pytest.raises(InvalidInputError, match="does not exist"):
        bridge._write_csv_rows([{"a": 1}], str(target), overwrite=True,
                               create_parent=False)
    assert not missing_dir.exists()
