"""Annotation-object addressing (targets.iter_annotations/
find_annotation_by_id, and the move_objects/transform fix built on top --
find_annotation_by_id_any/find_removable_by_id/resolve_any, plus
bridge._Layout._move_annotation/_annotation_bounds) -- pure, no COM.
Self-contained fakes, not shared with test_targets_refs.py/
test_targets_cache.py, since tests/ isn't a package (no relative imports).

ChemDrawBridge() is safe to construct directly here (used below to exercise
the real _move_annotation/_annotation_bounds/_set_position, not a
reimplementation of them): Connection/ComWorker are both lazy and only
touch COM/spawn threads once a tool call actually runs — same precedent
already established in test_snapshot_debounce.py."""
import pytest

from chemdraw_connector import targets
from chemdraw_connector.bridge import ChemDrawBridge
from chemdraw_connector.errors import TargetNotFoundError


class FakeTag:
    def __init__(self):
        self.StringValue = None


class FakeAnnotation:
    def __init__(self):
        self._tags = {}

    def GetObjectTag(self, name):
        return self._tags.get(name)

    def MakeObjectTag(self, name, _flag):
        tag = FakeTag()
        self._tags[name] = tag
        return tag


class Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class FakeDoc:
    def __init__(self, arrows=(), symbols=(), brackets=()):
        self.Arrows = Coll(arrows)
        self.Symbols = Coll(symbols)
        self.Brackets = Coll(brackets)


def test_iter_annotations_arrows():
    a1, a2 = FakeAnnotation(), FakeAnnotation()
    doc = FakeDoc(arrows=[a1, a2])
    assert targets.iter_annotations(doc, "arrow") == [a1, a2]


def test_iter_annotations_brackets():
    b1, b2 = FakeAnnotation(), FakeAnnotation()
    doc = FakeDoc(brackets=[b1, b2])
    assert targets.iter_annotations(doc, "bracket") == [b1, b2]


def test_find_annotation_by_id_matches_tagged_bracket():
    b1, b2 = FakeAnnotation(), FakeAnnotation()
    oid = targets.ensure_id(b2)
    doc = FakeDoc(brackets=[b1, b2])
    assert targets.find_annotation_by_id(doc, "bracket", oid) is b2


def test_iter_annotations_symbols_empty():
    doc = FakeDoc()
    assert targets.iter_annotations(doc, "symbol") == []


def test_find_annotation_by_id_matches_tagged_object():
    a1, a2 = FakeAnnotation(), FakeAnnotation()
    oid = targets.ensure_id(a2)
    doc = FakeDoc(arrows=[a1, a2])
    assert targets.find_annotation_by_id(doc, "arrow", oid) is a2


def test_find_annotation_by_id_unknown_raises_target_not_found():
    doc = FakeDoc(arrows=[FakeAnnotation()])
    with pytest.raises(TargetNotFoundError):
        targets.find_annotation_by_id(doc, "arrow", "claude-nope")


def test_find_annotation_by_id_empty_id_raises_target_not_found():
    doc = FakeDoc(arrows=[FakeAnnotation()])
    with pytest.raises(TargetNotFoundError):
        targets.find_annotation_by_id(doc, "arrow", "")


def test_annotations_use_same_tagging_mechanism_as_units():
    """ensure_id/get_id are generic (not Group-specific) -- proven live on
    Caption already (tag_caption_owner); this just confirms the plain fake
    object shape used here round-trips through them too."""
    a = FakeAnnotation()
    oid = targets.ensure_id(a)
    assert oid.startswith("claude-")
    assert targets.get_id(a) == oid


# ---------- find_removable_by_id / find_annotation_by_id_any / resolve_any ----------
# Covers the actual bug this session fixed: chemdraw_move_objects/
# chemdraw_transform used to resolve targets through structures only
# (targets.resolve/find_by_id/iter_units), so a real arrow or an unowned
# caption -- confirmed present via iter_annotations/find_annotation_by_id,
# i.e. exactly what chemdraw_list_arrows itself calls -- came back
# "missing" from move_objects, and transform raised a TargetNotFoundError
# claiming it "no longer exists ... may have been deleted or modified by
# hand" for an object that had just been confirmed present. resolve_any/
# find_removable_by_id are the shared fix both tools now go through.

class FakeStructUnit:
    """Minimal structure-unit fake -- same shape as
    test_targets_resolve.py's FakeUnit, duplicated here (not imported)
    since tests/ isn't a package and this file's own convention is
    self-contained fakes."""

    def __init__(self, uid, atom_count=0, bond_count=0):
        self.ID = uid
        self.Objects = type("O", (), {
            "Atoms": type("C", (), {"Count": atom_count})(),
            "Bonds": type("C", (), {"Count": bond_count})(),
        })()
        self._tags = {}

    def GetObjectTag(self, name):
        return self._tags.get(name)

    def MakeObjectTag(self, name, _flag):
        tag = FakeTag()
        self._tags[name] = tag
        return tag


class FakeSelection:
    def __init__(self, groups):
        self.Groups = Coll(groups)


class FullFakeDoc:
    """A FakeDoc covering both structures (Groups/Atoms/Bonds/Selection)
    and annotations (Arrows/Captions/Symbols/Brackets/TLCPlates) in the
    SAME document -- resolve_any needs both present at once to prove it
    can tell a structure id from an annotation id rather than just
    matching whichever collection is non-empty."""

    def __init__(self, groups=(), atoms=(), bonds=(), arrows=(), captions=(),
                selection=None, name="doc1"):
        self.Groups = Coll(list(groups))
        self.Atoms = Coll(list(atoms))
        self.Bonds = Coll(list(bonds))
        self.Arrows = Coll(list(arrows))
        self.Captions = Coll(list(captions))
        self.Symbols = Coll([])
        self.Brackets = Coll([])
        self.TLCPlates = Coll([])
        self.Selection = selection
        self.name = name


def test_find_removable_by_id_finds_structure_before_annotations():
    grp = FakeStructUnit(1)
    oid = targets.ensure_id(grp)
    doc = FullFakeDoc(groups=[grp], arrows=[FakeAnnotation()])
    kind, obj = targets.find_removable_by_id(doc, oid)
    assert (kind, obj) == ("structure", grp)


def test_find_removable_by_id_falls_through_to_arrow():
    """The exact bug scenario: an arrow that chemdraw_list_arrows can see
    (iter_annotations) must resolve here too, not just for structures."""
    arrow = FakeAnnotation()
    oid = targets.ensure_id(arrow)
    doc = FullFakeDoc(arrows=[arrow])
    kind, obj = targets.find_removable_by_id(doc, oid)
    assert (kind, obj) == ("arrow", arrow)


def test_find_removable_by_id_falls_through_to_unowned_caption():
    """A caption with NO owning structure (targets.NO_CAPTION_OWNER) --
    e.g. a reaction scheme's reagents_text/conditions_text -- must still
    resolve by its OWN claude_id, independent of caption-ownership
    status; ownership and addressability are separate mechanisms."""
    cap = FakeAnnotation()
    targets.tag_caption_owner(cap, targets.NO_CAPTION_OWNER)
    oid = targets.ensure_id(cap)
    doc = FullFakeDoc(captions=[cap])
    kind, obj = targets.find_removable_by_id(doc, oid)
    assert (kind, obj) == ("caption", cap)
    assert targets.get_caption_owner(cap) == targets.NO_CAPTION_OWNER


def test_find_removable_by_id_raises_when_truly_absent():
    doc = FullFakeDoc()
    with pytest.raises(TargetNotFoundError):
        targets.find_removable_by_id(doc, "claude-nope")


def test_find_annotation_by_id_any_matches_any_kind():
    cap = FakeAnnotation()
    oid = targets.ensure_id(cap)
    doc = FullFakeDoc(captions=[cap])
    kind, obj = targets.find_annotation_by_id_any(doc, oid)
    assert (kind, obj) == ("caption", cap)


def test_find_annotation_by_id_any_empty_id_raises():
    doc = FullFakeDoc()
    with pytest.raises(TargetNotFoundError):
        targets.find_annotation_by_id_any(doc, "")


def test_resolve_any_document_returns_only_structures():
    """"document" must keep returning structures only -- describe_canvas
    and friends assume that shape (see iter_annotations' docstring)."""
    grp = FakeStructUnit(1)
    targets.ensure_id(grp)
    arrow = FakeAnnotation()
    targets.ensure_id(arrow)
    doc = FullFakeDoc(groups=[grp], arrows=[arrow])
    assert targets.resolve_any(doc, "document") == [("structure", grp)]


def test_resolve_any_selection_returns_only_structures():
    grp = FakeStructUnit(1, atom_count=1)
    targets.ensure_id(grp)
    doc = FullFakeDoc(groups=[grp], selection=FakeSelection(groups=[grp]))
    assert targets.resolve_any(doc, "selection") == [("structure", grp)]


def test_resolve_any_explicit_id_resolves_structure():
    grp = FakeStructUnit(1)
    oid = targets.ensure_id(grp)
    doc = FullFakeDoc(groups=[grp])
    assert targets.resolve_any(doc, oid) == [("structure", grp)]


def test_resolve_any_explicit_id_resolves_arrow():
    arrow = FakeAnnotation()
    oid = targets.ensure_id(arrow)
    doc = FullFakeDoc(arrows=[arrow])
    assert targets.resolve_any(doc, oid) == [("arrow", arrow)]


def test_resolve_any_explicit_id_resolves_unowned_caption():
    cap = FakeAnnotation()
    targets.tag_caption_owner(cap, targets.NO_CAPTION_OWNER)
    oid = targets.ensure_id(cap)
    doc = FullFakeDoc(captions=[cap])
    assert targets.resolve_any(doc, oid) == [("caption", cap)]


def test_resolve_any_list_mixes_structure_and_annotation():
    grp = FakeStructUnit(1)
    grp_id = targets.ensure_id(grp)
    arrow = FakeAnnotation()
    arrow_id = targets.ensure_id(arrow)
    doc = FullFakeDoc(groups=[grp], arrows=[arrow])
    resolved = targets.resolve_any(doc, [grp_id, arrow_id])
    assert resolved == [("structure", grp), ("arrow", arrow)]


def test_resolve_any_raises_for_id_that_matches_nothing():
    doc = FullFakeDoc()
    with pytest.raises(TargetNotFoundError):
        targets.resolve_any(doc, "claude-nope")


# ---------- bridge._Layout._move_annotation / _annotation_bounds ----------
# Proves the move itself, not just resolution: dx/dy actually changes an
# annotation's position, using the exact per-kind strategy landed on (see
# _move_annotation's own docstring in chemdraw_connector/bridge/_layout.py)
# -- Position for caption/symbol, BOTH Start and End for arrow/bracket (a
# single-endpoint update would distort/rotate rather than translate, since
# Arrow.Position is confirmed live to alias .End, not a true center/anchor).

class FakePoint:
    def __init__(self, x, y):
        self.X = x
        self.Y = y


class FakePositionalAnnotation(FakeAnnotation):
    """Caption/Symbol shape: a single .Position anchor point. Mirrors real
    COM struct-by-value semantics -- the getter returns a FRESH point each
    time, so mutating it does nothing until explicitly written back via
    the setter -- the same reason production _set_position/_move_annotation
    read-mutate-reassign instead of poking obj.Position.X directly."""

    def __init__(self, x=0.0, y=0.0):
        super().__init__()
        self._x, self._y = x, y

    @property
    def Position(self):
        return FakePoint(self._x, self._y)

    @Position.setter
    def Position(self, pt):
        self._x, self._y = pt.X, pt.Y


class FakeEndpointedAnnotation(FakeAnnotation):
    """Arrow/Bracket shape: independent .Start/.End points, same
    struct-by-value getter/setter semantics as above."""

    def __init__(self, sx=0.0, sy=0.0, ex=10.0, ey=0.0):
        super().__init__()
        self._sx, self._sy, self._ex, self._ey = sx, sy, ex, ey

    @property
    def Start(self):
        return FakePoint(self._sx, self._sy)

    @Start.setter
    def Start(self, pt):
        self._sx, self._sy = pt.X, pt.Y

    @property
    def End(self):
        return FakePoint(self._ex, self._ey)

    @End.setter
    def End(self, pt):
        self._ex, self._ey = pt.X, pt.Y


class FakeBoundedAnnotation(FakeAnnotation):
    def __init__(self, left, top, right, bottom):
        super().__init__()
        self.Left, self.Top, self.Right, self.Bottom = left, top, right, bottom


def test_move_annotation_translates_caption_position():
    cap = FakePositionalAnnotation(x=100.0, y=200.0)
    ok = ChemDrawBridge()._move_annotation("caption", cap, 12.0, -8.0)
    assert ok is True
    assert (cap._x, cap._y) == (112.0, 192.0)


def test_move_annotation_translates_symbol_position():
    sym = FakePositionalAnnotation(x=0.0, y=0.0)
    ok = ChemDrawBridge()._move_annotation("symbol", sym, 5.0, 5.0)
    assert ok is True
    assert (sym._x, sym._y) == (5.0, 5.0)


def test_move_annotation_translates_arrow_start_and_end_together():
    """The bug's exact demonstrated failure mode: chemdraw_move_objects
    with dx=0, dy=170 on a real arrow. Both endpoints must shift by the
    identical delta -- proving this doesn't just drag one end (which would
    distort/rotate rather than translate)."""
    arrow = FakeEndpointedAnnotation(sx=0.0, sy=0.0, ex=50.0, ey=0.0)
    ok = ChemDrawBridge()._move_annotation("arrow", arrow, 0.0, 170.0)
    assert ok is True
    assert (arrow._sx, arrow._sy) == (0.0, 170.0)
    assert (arrow._ex, arrow._ey) == (50.0, 170.0)


def test_move_annotation_translates_bracket_start_and_end_together():
    br = FakeEndpointedAnnotation(sx=10.0, sy=10.0, ex=10.0, ey=90.0)
    ok = ChemDrawBridge()._move_annotation("bracket", br, 20.0, 0.0)
    assert ok is True
    assert (br._sx, br._sy) == (30.0, 10.0)
    assert (br._ex, br._ey) == (30.0, 90.0)


def test_move_annotation_unknown_kind_returns_false_without_raising():
    ok = ChemDrawBridge()._move_annotation("structure", FakeAnnotation(), 1.0, 1.0)
    assert ok is False


def test_move_annotation_swallows_exception_and_returns_false():
    class Broken(FakeAnnotation):
        @property
        def Position(self):
            raise RuntimeError("COM object gone")

    ok = ChemDrawBridge()._move_annotation("caption", Broken(), 1.0, 1.0)
    assert ok is False


def test_annotation_bounds_reads_left_top_right_bottom():
    obj = FakeBoundedAnnotation(1.0, 2.0, 3.0, 4.0)
    assert ChemDrawBridge()._annotation_bounds(obj) == {
        "left": 1.0, "top": 2.0, "right": 3.0, "bottom": 4.0,
    }


def test_annotation_bounds_returns_none_when_unreadable():
    assert ChemDrawBridge()._annotation_bounds(FakeAnnotation()) is None
