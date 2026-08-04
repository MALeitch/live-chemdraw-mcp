"""Ground-truth check: cdx_binary vs ChemDraw's own .cdx -> .cdxml conversion.

SKIPPED unless the fixtures exist locally, same convention as
`tests/test_naming.py` (which skips unless OPSIN is set up). The fixtures are
multi-MB real documents and are deliberately NOT checked in.

To regenerate (needs ChemDraw running):

    chemdraw_convert_cdx_cdxml(
        r"...\\Notebook Data\\Table of Contents MAL A.cdxml",
        r"...\\prototypes\\cdx\\TOC_MAL_A.cdx")

That direction matters: converting a CDXML that already contains wedge/hash
bonds gives a .cdx guaranteed to exercise stereo, whereas most .cdx files on
hand turned out to carry no Display property at all. The first two files tried
during development had 0 and 3 stereo bonds respectively -- enough to look
like a passing validation while testing almost nothing.

This is the check that pins the kCDXProp_Bond_Display enum. Those integer
codes were originally written from memory; the values matter because a wrong
one silently turns a wedge into a plain line, and a flat structure looks
entirely reasonable on screen.
"""
import collections
import os

import pytest

import xml.etree.ElementTree as ET

from chemdraw_connector.domain.cdx_binary import parse_file

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURES = os.path.join(os.path.dirname(_HERE), "prototypes", "cdx")

# (ChemDraw-authored CDXML = ground truth, our .cdx conversion of it)
_PAIRS = [
    (r"C:\Users\micha\Documents\School\Pitt PhD\Notebook Data\Table of Contents MAL A.cdxml",
     os.path.join(_FIXTURES, "TOC_MAL_A.cdx")),
    (r"C:\Users\micha\Documents\School\Pitt PhD\_Email Attachments\Current scope.cdxml",
     os.path.join(_FIXTURES, "CurrentScope.cdx")),
]

_AVAILABLE = [(x, c) for x, c in _PAIRS
              if os.path.exists(x) and os.path.exists(c)]

pytestmark = pytest.mark.skipif(
    not _AVAILABLE,
    reason="ground-truth fixtures not present; see module docstring to regenerate",
)


def _bonds_from_cdx(path):
    out = {}
    for frag in parse_file(path):
        for b in frag["bonds"]:
            out[(str(b["begin"]), str(b["end"]))] = b["display"]
    return out


def _bonds_from_cdxml(path):
    out = {}
    for b in ET.parse(path).getroot().iter("b"):
        begin, end = b.get("B"), b.get("E")
        if begin and end:
            out[(begin, end)] = b.get("Display")   # absent -> None
    return out


@pytest.mark.parametrize("cdxml,cdx", _AVAILABLE,
                         ids=lambda p: os.path.basename(str(p)))
def test_bond_display_matches_chemdraws_own_output(cdxml, cdx):
    mine = _bonds_from_cdx(cdx)
    truth = _bonds_from_cdxml(cdxml)
    shared = set(mine) & set(truth)
    assert shared, "no bonds in common -- fixture pair is mismatched"

    wrong = {k: (mine[k], truth[k]) for k in shared if mine[k] != truth[k]}
    assert not wrong, (
        f"{len(wrong)} of {len(shared)} bonds disagree with ChemDraw; "
        f"first few: {list(wrong.items())[:5]}"
    )


@pytest.mark.parametrize("cdxml,cdx", _AVAILABLE,
                         ids=lambda p: os.path.basename(str(p)))
def test_both_front_ends_agree_on_the_same_document(cdxml, cdx):
    """The same drawing read as .cdxml and as .cdx must give identical bonds.

    These two parsers were written months apart and feed the same
    cdxml_document/canvas pipeline, so drift between them is invisible until
    someone compares results across formats. It has already happened twice:
    `order` diverged (string vs int, silently degrading every bond to SINGLE)
    and `display` existed on the .cdx side only, so one format reported
    wedges while the other reported flat bonds for the same molecule.
    """
    from chemdraw_connector.domain import cdxml_graph

    with open(cdxml, encoding="utf-8") as fh:
        xml_bonds = {(b["begin"], b["end"]): (b["order"], b["display"])
                     for b in cdxml_graph.parse(fh.read())["bonds"]}
    cdx_bonds = {}
    for frag in parse_file(cdx):
        for b in frag["bonds"]:
            cdx_bonds[(b["begin"], b["end"])] = (b["order"], b["display"])

    shared = set(xml_bonds) & set(cdx_bonds)
    assert shared, "no bonds in common -- fixture pair is mismatched"
    differing = {k: (xml_bonds[k], cdx_bonds[k])
                 for k in shared if xml_bonds[k] != cdx_bonds[k]}
    assert not differing, (
        f"{len(differing)} of {len(shared)} bonds differ between the CDXML and "
        f"CDX front ends (cdxml, cdx); first few: "
        f"{list(differing.items())[:5]}"
    )


def test_the_fixtures_actually_contain_stereo():
    """Guards the validation itself.

    A pair with no wedge bonds passes the check above trivially while proving
    nothing about the enum -- which is exactly what happened twice during
    development. If this fails, the fixtures have drifted and the agreement
    result above is worthless.
    """
    kinds = collections.Counter()
    for cdxml, _cdx in _AVAILABLE:
        for display in _bonds_from_cdxml(cdxml).values():
            if display:
                kinds[display] += 1

    assert kinds, "fixtures contain no stereo bonds at all"
    assert {"WedgeBegin", "WedgedHashBegin"} <= set(kinds), (
        f"fixtures must exercise real wedge AND hash bonds; got {dict(kinds)}"
    )
