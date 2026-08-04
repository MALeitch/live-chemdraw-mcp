"""Tests for .cdx -> canvas integration (no COM, no live ChemDraw).

The point of most of these is the difference between "checked, found nothing"
and "could not check". `violations` is the field ROADMAP #7 tells every caller
to inspect before trusting a page, so an empty list there is read as "this page
is clean". When geometry is unavailable that answer is not merely unknown, it
is actively misleading -- an overlapping or off-page structure would be
reported the same way as a tidy one.
"""
import struct

from chemdraw_connector.domain.cdx_document import parse_cdx_document

from test_cdx_binary import (
    O_FRAGMENT, _page, _obj, _atom, _bond,
)

# geometry-dependent violation keys: each answers a question you can only ask
# of a drawing that has coordinates
_GEOMETRY_KEYS = ("overlapping_structures", "overflowing_box", "off_page",
                  "stoichiometry_grid_overlaps")


def _ethanol_page():
    """C-C-O, no coordinates."""
    return _page(_obj(O_FRAGMENT, 10,
                      _atom(11, 6) + _atom(12, 6) + _atom(13, 8)
                      + _bond(14, 11, 12) + _bond(15, 12, 13)))


def _ethanol_page_with_coords():
    """Same molecule, drawn at real positions."""
    return _page(_obj(O_FRAGMENT, 10,
                      _atom(11, 6, pos=(0.0, 0.0))
                      + _atom(12, 6, pos=(30.0, 15.0))
                      + _atom(13, 8, pos=(60.0, 0.0))
                      + _bond(14, 11, 12) + _bond(15, 12, 13)))


def _write(tmp_path, data, name="page.cdx"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_structures_and_formula_are_recovered(tmp_path):
    res = parse_cdx_document(_write(tmp_path, _ethanol_page()))
    assert len(res["structures"]) == 1
    assert res["structures"][0]["formula"] == "C2H6O"
    assert res["structures"][0]["atom_count"] == 3
    assert res["structures"][0]["bond_count"] == 2


def test_violations_are_null_not_empty_when_geometry_is_missing(tmp_path):
    """An empty list here means 'no problems'. Without coordinates we cannot
    know that, so the honest answer is None."""
    res = parse_cdx_document(_write(tmp_path, _ethanol_page()))
    for key in _GEOMETRY_KEYS:
        assert res["violations"].get(key) is None, (
            f"{key} must be None when no structure has bounds -- an empty "
            f"list is indistinguishable from a genuinely clean page"
        )


def test_a_note_explains_why_violations_are_unavailable(tmp_path):
    res = parse_cdx_document(_write(tmp_path, _ethanol_page()))
    note = res.get("violations_note") or ""
    assert note, "an unavailable check needs to say why"
    assert "coordinate" in note.lower() or "geometry" in note.lower()


def test_page_bounds_is_none_without_geometry(tmp_path):
    res = parse_cdx_document(_write(tmp_path, _ethanol_page()))
    assert res["page_bounds"] is None


def test_geometry_is_recovered_when_the_drawing_has_coordinates(tmp_path):
    """CDX carries 2DPosition; it should reach the structure's bounds."""
    res = parse_cdx_document(_write(tmp_path, _ethanol_page_with_coords()))
    bounds = res["structures"][0]["bounds"]
    assert bounds is not None, "2DPosition is present and must not be discarded"
    assert bounds["left"] < bounds["right"]
    assert bounds["top"] < bounds["bottom"]


def test_violations_are_computed_once_geometry_exists(tmp_path):
    """With coordinates the checks become real, so empty means empty."""
    res = parse_cdx_document(_write(tmp_path, _ethanol_page_with_coords()))
    for key in _GEOMETRY_KEYS:
        assert isinstance(res["violations"].get(key), list), (
            f"{key} should be a real (possibly empty) result once bounds exist"
        )
    assert not res.get("violations_note")


def test_empty_blob_yields_no_structures(tmp_path):
    """A Ctrl+V paste with no chemistry is routine, not an error."""
    res = parse_cdx_document(_write(tmp_path, b"no cdx payload here"))
    assert res["structures"] == []
