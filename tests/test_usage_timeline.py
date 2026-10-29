"""Chart exploration conserves counters and wall time across grouping and zoom."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from usage_fixtures import accounting, event, summary

from flowfield.usage_models import Tokens, UsageFilter
from flowfield.usage_reads import buckets
from flowfield.usage_timeline import Timeline


def test_dimensions_preserve_full_selection_and_frozen_model(tmp_path, monkeypatch):
    execution, usage, runs = accounting(tmp_path, count=2)
    monkeypatch.setattr("flowfield.application.now", lambda: "2026-10-27T10:10:00+00:00")
    for run in runs:
        usage.observe("harbor", "run", run.id, event(run))
    with execution.workspace.connection(write=True) as db:
        for index, run in enumerate(runs):
            db.execute(
                "UPDATE usage_executions SET role=?,harness=?,requested_model=?,"
                "started_at='2026-10-27T10:00:00+00:00',"
                "ended_at='2026-10-27T10:20:00+00:00',timing='recorded' WHERE source_id=?",
                (
                    "worker" if index == 0 else "reviewer",
                    "codex" if index == 0 else "pi",
                    f"selected-{index}",
                    run.id,
                ),
            )
    for dimension, keys in (
        ("role", {"worker", "reviewer"}),
        ("harness", {"codex", "pi"}),
        ("model", {"selected-0", "selected-1"}),
    ):
        report = summary(
            execution,
            start="2026-10-27T10:00:00Z",
            end="2026-10-27T11:00:00Z",
            group="minute",
            dimension=dimension,
            limit=1,
        )
        assert len(report.items) == 1
        assert {s.key for s in report.series} == keys
        assert sum(p.tokens.total or 0 for s in report.series for p in s.points) == 360
        assert sum(b.tokens.total or 0 for b in report.buckets) == 360
        assert report.totals.elapsed_seconds == 1200
        assert sum(b.elapsed_seconds or 0 for b in report.buckets) == 1200
        # Group clocks overlap; they must never be stacked into total duration.
        assert sum(p.elapsed_seconds or 0 for s in report.series for p in s.points) == 2400
        assert all(s.points[10].tokens.total == 180 for s in report.series)
        assert all(s.points[9].tokens.total is None for s in report.series)


def test_many_series_other_preserves_tokens_and_unions_duration():
    start = datetime(2026, 10, 27, tzinfo=UTC)
    timeline = Timeline(buckets(UsageFilter(start=start, end=start + timedelta(hours=1))))
    for index in range(10):
        timeline.receipt(f"model-{index}", start, Tokens(total=10 - index))
        timeline.interval(f"model-{index}", start, start + timedelta(minutes=1))
    series = timeline.series()
    assert len(series) == 8
    other = series[-1]
    assert other.other and other.key is None
    assert other.points[0].tokens.total == 6
    assert other.points[0].elapsed_seconds == 60
    assert sum(s.points[0].tokens.total for s in series) == 55
    assert timeline.totals()[0].elapsed_seconds == 60


def test_hour_zoom_preserves_repeated_local_hour_and_half_open_receipts():
    for start in ("2026-11-01T07:00:00Z", "2026-11-01T08:00:00Z"):
        left = datetime.fromisoformat(start)
        points = buckets(
            UsageFilter(
                start=left, end=left + timedelta(hours=1), timezone="America/Denver", group="minute"
            )
        )
        assert len(points) == 60
        timeline = Timeline(points)
        timeline.receipt(None, left, Tokens(total=10))
        timeline.receipt(None, left + timedelta(minutes=1), Tokens(total=20))
        timeline.receipt(None, left + timedelta(hours=1), Tokens(total=999))
        series = timeline.series()[0]
        assert series.key is None and not series.other
        assert [p.tokens.total for p in series.points[:3]] == [10, 20, None]
        assert sum(p.tokens.total or 0 for p in series.points) == 30
    with pytest.raises(ValidationError, match="Minute"):
        UsageFilter(start="2026-10-27T00:00:00Z", end="2026-10-29T00:00:00Z", group="minute")
