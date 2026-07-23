"""Annotation-object addressing (targets.iter_annotations/
find_annotation_by_id) -- pure, no COM. Self-contained fakes, not shared
with test_targets_refs.py/test_targets_cache.py, since tests/ isn't a
package (no relative imports)."""
import pytest

from chemdraw_connector import targets
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
