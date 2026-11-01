"""Read reuse must preserve authoritative ledger changes and bounded working sets."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from usage_fixtures import accounting, event

from flowfield.usage_models import UsageFilter
from flowfield.usage_read_cache import UsageReadCache
from flowfield.usage_reads import UsageReads


def content(view):
    return view.model_dump(exclude={"measured_at"})


@pytest.mark.parametrize("change", ["receipt", "retime", "cost", "owner", "delete"])
def test_reuse_matches_fresh_reads_after_ledger_changes(tmp_path, monkeypatch, change):
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe("harbor", "run", run.id, event(run))
    usage.end("harbor", "run", run.id, confirmed=True)
    store = execution.workspace
    reads = UsageReads(store)
    filters = [
        UsageFilter(),
        UsageFilter(model="reported-model"),
        UsageFilter(coverage="complete", limit=1),
        UsageFilter(start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
    ]
    for query in filters:
        assert content(reads.view("harbor", query)) == content(reads.view("harbor", query))
    if change == "receipt":
        usage.observe("harbor", "run", run.id, event(run, 200, identity="late"))
    else:
        with store.connection(write=True, notify=False) as db:
            db.execute(
                {
                    "retime": "UPDATE usage_observations "
                    "SET received_at='2026-01-01T12:00:00+00:00'",
                    "cost": "UPDATE usage_observations SET cost=NULL",
                    "owner": "UPDATE usage_executions "
                    "SET reasons='[\"changed\"]', outcome='stopped'",
                    "delete": "DELETE FROM usage_observations",
                }[change]
            )
    reused = [content(reads.view("harbor", query)) for query in filters]
    monkeypatch.setattr(store, "usage_read_cache", UsageReadCache(max_bytes=0))
    assert reused == [content(reads.view("harbor", query)) for query in filters]


def test_cached_models_are_not_exposed_to_callers(tmp_path):
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe("harbor", "run", run.id, event(run))
    usage.end("harbor", "run", run.id, confirmed=True)
    reads = UsageReads(execution.workspace)
    first = reads.view("harbor", UsageFilter())
    expected = content(first)
    first.items[0].reasons.append("caller mutation")
    first.items[0].observed_models.clear()
    first.items[0].tokens.total = 999
    first.totals.cost.reasons.append("caller mutation")
    assert content(reads.view("harbor", UsageFilter())) == expected


def test_scan_over_budget_keeps_reusable_subset_and_concurrent_reads_agree(tmp_path, monkeypatch):
    execution, usage, runs = accounting(tmp_path, count=3)
    for run in runs:
        usage.observe("harbor", "run", run.id, event(run))
        usage.end("harbor", "run", run.id, confirmed=True)
    cache = UsageReadCache(max_receipts=1)
    monkeypatch.setattr(execution.workspace, "usage_read_cache", cache)
    reads = UsageReads(execution.workspace)
    import flowfield.usage_reads as module

    original = module.execution_record
    built = []

    def build(row, observations):
        built.append(row["number"])
        return original(row, observations)

    monkeypatch.setattr(module, "execution_record", build)
    expected = content(reads.view("harbor", UsageFilter()))
    assert len(built) == 3
    built.clear()
    assert content(reads.view("harbor", UsageFilter())) == expected
    assert len(built) == 2  # Oversized scans do not evict the entire working set.
    assert cache.receipts == 1 and cache.bytes <= cache.max_bytes
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(
            value == expected
            for value in pool.map(lambda _: content(reads.view("harbor", UsageFilter())), range(8))
        )


def test_cache_budget_and_owner_isolation(tmp_path):
    first, usage, (run,) = accounting(tmp_path / "first")
    usage.observe("harbor", "run", run.id, event(run))
    second, other_usage, (other,) = accounting(tmp_path / "second")
    other_usage.observe("harbor", "run", other.id, event(other, 500))
    first.workspace.usage_read_cache = UsageReadCache(max_bytes=1)
    assert UsageReads(first.workspace).view("harbor", UsageFilter()).totals.tokens.total == 180
    assert UsageReads(second.workspace).view("harbor", UsageFilter()).totals.tokens.total == 900
    assert first.workspace.usage_read_cache.bytes == 0
    assert first.workspace.usage_read_cache.receipts == 0
