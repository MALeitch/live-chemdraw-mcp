"""transform(action="clean") iterates per structure, and threads deNovo. No COM.

Measured on real files: ChemDraw's Clean(False) against a collection holding
24 structures burned 785 s of CPU without returning and left the COM queue
blocked; cleaning the same 24 one at a time took 0.8 s total, median 0.01 s.
Clean appears to treat a multi-structure selection as one system to lay out
together, so cost grows superlinearly with page size.

The regression these guard against is therefore not "clean produces a wrong
drawing" — it is "clean was handed the whole page in a single call". That is
invisible in any assertion about the returned dict, so the tests below count
Clean() invocations and check WHICH objects each one received.

The same applies to Clean's own deNovo argument, which the connector used to
hardcode to False. de_novo=True is the difference between "tidy what is drawn"
and "throw the drawing away and re-derive it", and NEITHER produces an error or
an odd-looking return value — the only evidence of which one ran is the boolean
that reached Clean(). So `clean_flags` records that argument per call, and the
tests assert on it rather than on the response dict.
"""
import pytest

from chemdraw_connector.bridge import _manipulation as mp
from chemdraw_connector.errors import InvalidInputError


class FakeObjects:
    """One structure's own IChemDrawObjects collection."""

    def __init__(self, uid, log, flags, boom=False):
        self.uid = uid
        self._log = log
        self._flags = flags
        self._boom = boom

    def Clean(self, flag):
        if self._boom:
            raise RuntimeError(f"ChemDraw refused to clean {self.uid}")
        self._log.append(self.uid)
        self._flags.append(flag)


class FakeUnit:
    def __init__(self, uid, boom=False):
        self.uid = uid
        self.boom = boom


class FakeManipulation(mp._Manipulation):
    def __init__(self):
        self._doc_obj = object()

    def _doc(self):
        return self._doc_obj

    def _cache_for(self, doc):
        return {}

    def _run(self, fn, timeout=None, op_name=None, op_description=None):
        return fn()


@pytest.fixture
def clean_log(monkeypatch):
    """Patch target resolution; return the list Clean() calls append to."""
    log = []
    flags = []

    def _install(resolved):
        monkeypatch.setattr(mp.targets, "resolve_any",
                            lambda doc, target, cache: resolved)
        monkeypatch.setattr(mp.targets, "ensure_id", lambda obj: obj.uid)
        monkeypatch.setattr(
            mp.targets, "unit_objects",
            lambda obj: FakeObjects(obj.uid, log, flags,
                                    boom=getattr(obj, "boom", False)))
        return log, flags

    return _install


def _structures(*uids):
    return [("structure", FakeUnit(u)) for u in uids]


def test_many_structures_are_cleaned_one_at_a_time(clean_log):
    """The load-bearing test: N structures must mean N Clean() calls."""
    log, _ = clean_log(_structures("s1", "s2", "s3", "s4"))
    res = FakeManipulation().transform(target="document", action="clean")

    assert log == ["s1", "s2", "s3", "s4"], (
        "each structure must get its own Clean(); one call covering the whole "
        "page is the superlinear path this fix exists to avoid"
    )
    assert res["transformed"] == ["s1", "s2", "s3", "s4"]
    assert res["failed"] == []


def test_each_clean_receives_only_its_own_unit(clean_log):
    """Per-unit means per-unit objects, not the page collection N times."""
    seen = []
    log, _ = clean_log(_structures("a", "b"))

    original = mp.targets.unit_objects

    def spy(obj):
        seen.append(obj.uid)
        return original(obj)

    mp.targets.unit_objects = spy
    try:
        FakeManipulation().transform(target="document", action="clean")
    finally:
        mp.targets.unit_objects = original

    assert seen == ["a", "b"]
    assert log == ["a", "b"]


def test_one_failing_structure_does_not_abort_the_batch(clean_log):
    """Per-item isolation, same convention as edit_atoms/make_reaction_route."""
    units = [("structure", FakeUnit("ok1")),
             ("structure", FakeUnit("bad", boom=True)),
             ("structure", FakeUnit("ok2"))]
    log, _ = clean_log(units)

    res = FakeManipulation().transform(target="document", action="clean")

    assert log == ["ok1", "ok2"], "a mid-batch failure must not stop the rest"
    assert res["transformed"] == ["ok1", "ok2"]
    assert [f["id"] for f in res["failed"]] == ["bad"]
    assert "refused to clean" in res["failed"][0]["error"]


def test_annotations_are_reported_not_cleaned(clean_log):
    """Arrows/captions have no chemical structure for Clean to act on."""
    resolved = [("structure", FakeUnit("s1")),
                ("structure", FakeUnit("s2")),
                ("arrow", FakeUnit("arrow1"))]
    log, _ = clean_log(resolved)

    res = FakeManipulation().transform(target="document", action="clean")

    assert log == ["s1", "s2"], "an arrow must never reach Clean()"
    failed_ids = [f["id"] for f in res["failed"]]
    assert failed_ids == ["arrow1"]
    assert "arrow" in res["failed"][0]["error"]


def test_a_single_structure_still_cleans(clean_log):
    """The per-unit branch is for >1; one structure must not regress."""
    log, _ = clean_log(_structures("only"))
    FakeManipulation().transform(target="document", action="clean")
    assert log == ["only"]


def test_clean_of_only_annotations_cleans_nothing(clean_log):
    log, _ = clean_log([("caption", FakeUnit("cap1")), ("arrow", FakeUnit("arr1"))])
    res = FakeManipulation().transform(target="document", action="clean")
    assert log == []
    assert sorted(f["id"] for f in res["failed"]) == ["arr1", "cap1"]


# --- Clean(deNovo): which of the two clean modes actually ran ---------------
#
# deNovo=True re-derives the layout from the connection table and discards the
# chemist's own arrangement. Sending it when the caller did not ask silently
# destroys a hand-laid-out scheme; failing to send it when they DID ask leaves
# structures the weaker clean measurably cannot rescue (13/144 still badly
# drawn after deNovo=False, 0/144 after True). Both look like success.


def test_clean_defaults_to_tidy_not_relayout(clean_log):
    """Omitting de_novo must keep the pre-existing behaviour exactly."""
    _, flags = clean_log(_structures("s1", "s2", "s3"))
    FakeManipulation().transform(target="document", action="clean")
    assert flags == [False, False, False], (
        "the default must stay deNovo=False; True would silently discard "
        "every structure's own arrangement"
    )


def test_de_novo_reaches_every_unit_in_the_per_unit_batch(clean_log):
    """Per-unit iteration must not drop the flag on the way through."""
    log, flags = clean_log(_structures("s1", "s2", "s3"))
    res = FakeManipulation().transform(target="document", action="clean",
                                       de_novo=True)
    assert log == ["s1", "s2", "s3"]
    assert flags == [True, True, True]
    assert res["de_novo"] is True


def test_de_novo_reaches_the_single_structure_path(clean_log):
    """One structure skips the per-unit branch — a separate call site."""
    _, flags = clean_log(_structures("only"))
    res = FakeManipulation().transform(target="document", action="clean",
                                       de_novo=True)
    assert flags == [True]
    assert res["de_novo"] is True


def test_de_novo_is_echoed_so_the_caller_can_tell_which_mode_ran(clean_log):
    """The two modes are otherwise indistinguishable from the response."""
    clean_log(_structures("s1", "s2"))
    res = FakeManipulation().transform(target="document", action="clean")
    assert res["de_novo"] is False


def test_clean_flag_is_a_real_bool(clean_log):
    """Clean() is a COM call — hand it a bool, not something merely truthy."""
    _, flags = clean_log(_structures("s1"))
    FakeManipulation().transform(target="document", action="clean",
                                 de_novo="yes")
    assert flags == [True] and isinstance(flags[0], bool)


@pytest.mark.parametrize("action", ["move", "rotate", "scale", "flip"])
def test_de_novo_with_a_non_clean_action_is_rejected(clean_log, action):
    """Move/Rotate/Scale/Flip take no deNovo. Accepting it would promise a
    relayout that never happens — the exact shape of silent-success bug this
    module exists to catch."""
    log, _ = clean_log(_structures("s1"))
    with pytest.raises(InvalidInputError) as exc:
        FakeManipulation().transform(target="document", action=action,
                                     de_novo=True)
    assert "de_novo" in str(exc.value) and action in str(exc.value)
    assert log == []


def test_legacy_positional_callers_stay_on_the_tidy_path():
    """_layout's rotate/flip and _shorthand._clean_unit pass the original
    seven positional args. de_novo must default False for them, not inherit
    whatever a previous transform() call used."""
    seen = []

    class Objs:
        def Clean(self, flag):
            seen.append(flag)

    mp._Manipulation._apply_transform_action(
        Objs(), "clean", 0.0, 0.0, 0.0, 1.0, False)
    assert seen == [False]
