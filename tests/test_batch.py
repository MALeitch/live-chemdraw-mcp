"""domain.batch.run_batch -- issue #27. Pure Python, no COM."""
import time

from chemdraw_connector.domain import batch


def test_processes_everything_when_no_budget_or_limit():
    items = list(range(10))
    calls = []
    result = batch.run_batch(items, calls.append, id_fn=lambda i: f"id{i}")
    assert calls == items
    assert result["succeeded"] == [f"id{i}" for i in items]
    assert result["failed"] == []
    assert result["resume_at"] is None
    assert result["processed"] == 10
    assert result["total"] == 10


def test_limit_stops_early_and_sets_resume_at():
    items = list(range(10))
    calls = []
    result = batch.run_batch(items, calls.append, id_fn=lambda i: i, limit=4)
    assert calls == [0, 1, 2, 3]
    assert result["resume_at"] == 4
    assert result["processed"] == 4
    assert result["total"] == 10


def test_start_resumes_from_the_given_index():
    items = list(range(10))
    calls = []
    result = batch.run_batch(items, calls.append, id_fn=lambda i: i, start=4, limit=3)
    assert calls == [4, 5, 6]
    assert result["resume_at"] == 7


def test_resume_at_none_when_limit_reaches_the_end():
    items = list(range(5))
    calls = []
    result = batch.run_batch(items, calls.append, id_fn=lambda i: i, start=2, limit=10)
    assert calls == [2, 3, 4]
    assert result["resume_at"] is None


def test_per_item_failure_is_isolated_not_fatal():
    items = list(range(5))

    def work(i):
        if i == 2:
            raise ValueError("boom")

    result = batch.run_batch(items, work, id_fn=lambda i: f"id{i}")
    assert result["succeeded"] == ["id0", "id1", "id3", "id4"]
    assert result["failed"] == [{"id": "id2", "error": "boom"}]
    assert result["processed"] == 5
    assert result["resume_at"] is None


def test_budget_stops_before_the_next_item_and_sets_resume_at():
    items = list(range(20))
    calls = []

    def work(i):
        calls.append(i)
        if i == 2:
            time.sleep(0.05)  # blow the budget partway through

    result = batch.run_batch(items, work, id_fn=lambda i: i, budget=0.02)
    # Item 2 itself still ran (budget is checked BEFORE each item, never
    # mid-item) -- but item 3 never starts once the budget check trips.
    assert 2 in calls
    assert 3 not in calls
    assert result["resume_at"] == len(calls)
    assert result["processed"] == len(calls)


def test_budget_and_failure_combine_correctly():
    items = list(range(10))

    def work(i):
        if i == 1:
            raise RuntimeError("bad")
        if i == 3:
            time.sleep(0.05)

    result = batch.run_batch(items, work, id_fn=lambda i: i, budget=0.02)
    assert {"id": 1, "error": "bad"} in result["failed"]
    assert result["resume_at"] == 4
    assert result["processed"] == 4


def test_median_and_slowest_report():
    items = list(range(6))

    def work(i):
        time.sleep(0.001 * (i + 1))  # increasing durations

    result = batch.run_batch(items, work, id_fn=lambda i: i)
    assert result["median_seconds"] is not None
    assert result["median_seconds"] > 0
    assert len(result["slowest"]) == 5
    # Slowest-first ordering.
    assert result["slowest"][0]["id"] == 5


def test_empty_items_returns_clean_zero_result():
    result = batch.run_batch([], lambda i: None, id_fn=lambda i: i)
    assert result == {
        "succeeded": [], "failed": [], "resume_at": None,
        "processed": 0, "total": 0, "elapsed": result["elapsed"],
        "median_seconds": None, "slowest": [],
    }
