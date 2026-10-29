"""Production ledger, transaction, timing, attribution and access invariants."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from pydantic import ValidationError
from usage_fixtures import accounting, event, summary

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.usage import Usage, normalize
from flowfield.usage_models import Tokens, UsageFilter
from flowfield.usage_reads import UsageReads, buckets


def test_replay_reorder_restart_atomic_checkpoint_and_rebuild(tmp_path, monkeypatch):
    execution, usage, (run,) = accounting(tmp_path)
    first = event(run, order=1)
    usage.observe("harbor", "run", run.id, first)
    with execution.workspace.connection() as db:
        receipt = db.execute("SELECT received_at FROM usage_observations").fetchone()[0]
    usage = Usage(Workspace(execution.workspace.directory))
    usage.observe("harbor", "run", run.id, first.model_copy(update={"order": 50}))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: usage.observe("harbor", "run", run.id, first), range(8)))
    usage.observe("harbor", "run", run.id, event(run, 200, identity="result-3", order=3))
    usage.observe("harbor", "run", run.id, event(run, 150, identity="result-2", order=2))
    usage.rebuild()
    usage.observe("harbor", "run", run.id, event(run, 300, identity="result-4", order=4))
    detail = UsageReads(execution.workspace).evidence("harbor", "run", run.id)
    assert detail.items[0].received_at == receipt
    assert [item.delta.total for item in detail.items] == [180, 180, None, 180]
    assert detail.items[2].disposition == "reordered"
    assert summary(execution).totals.tokens.total == 540
    with execution.workspace.connection(write=True) as db:
        db.execute(
            "CREATE TRIGGER abort_checkpoint BEFORE UPDATE ON usage_checkpoints "
            "BEGIN SELECT RAISE(ABORT,'simulated crash'); END"
        )
    with pytest.raises(Exception, match="simulated crash"):
        usage.observe("harbor", "run", run.id, event(run, 400, identity="result-5"))
    assert summary(execution).totals.tokens.total == 540
    assert len(UsageReads(execution.workspace).evidence("harbor", "run", run.id).items) == 4
    with execution.workspace.connection(write=True) as db:
        db.execute("DROP TRIGGER abort_checkpoint")
    usage.observe("harbor", "run", run.id, event(run, 400, identity="result-5"))
    assert summary(execution).totals.tokens.total == 720


def test_components_inclusive_disjoint_optional_and_invalid(tmp_path):
    _, _, (run,) = accounting(tmp_path)
    assert normalize(event(run))[0] == Tokens(
        input=160, output=20, cache_read=50, cache_write=10, reasoning=10, total=180
    )
    inclusive = event(run, semantics="inclusive-cache")
    assert normalize(inclusive)[0].total == 120
    assert normalize(event(run, counters=Tokens(input=100, output=20)))[0].total is None
    assert (
        normalize(event(run, counters=Tokens(input=100, output=20, total=500)))[1] is None
    )  # Missing cache cannot reconcile.
    assert (
        normalize(
            event(
                run, counters=Tokens(input=100, output=20, total=500), semantics="inclusive-cache"
            )
        )[1]
        == "inconsistent-total"
    )
    assert (
        normalize(
            event(
                run, counters=Tokens(input=10, output=2, reasoning=3), semantics="inclusive-cache"
            )
        )[1]
        == "inconsistent-components"
    )
    for value in (-1, True, 1.5, float("inf")):
        with pytest.raises(ValidationError):
            Tokens(input=value)


def test_unknown_reset_conflicts_explicit_epoch_and_fork_baseline(tmp_path):
    execution, usage, (run, fork, unknown) = accounting(tmp_path, count=3)
    usage.observe("harbor", "run", run.id, event(run, 200))
    usage.observe("harbor", "run", run.id, event(run, 100, identity="regression"))
    usage.observe("harbor", "run", run.id, event(run, 300))  # Conflicting event ID.
    usage.observe(
        "harbor",
        "run",
        run.id,
        event(run, 50, identity="reset", epoch="clear-1", baseline="explicit-reset"),
    )
    usage.observe(
        "harbor",
        "run",
        fork.id,
        event(fork, 5300, baseline="fork", inherited=event(fork, 5000).counters),
    )
    usage.observe("harbor", "run", unknown.id, event(unknown, 5300, baseline="resume"))
    assert summary(execution).totals.tokens.total == 990  # 360+90+540, inherited 9000 excluded.
    detail = UsageReads(execution.workspace).evidence("harbor", "run", unknown.id)
    assert detail.execution.tokens.total is None
    assert detail.items[0].disposition == "unknown-baseline"
    assert summary(execution).totals.coverage == "partial"


def test_failure_stop_late_data_reminder_and_no_lifecycle_authority(tmp_path):
    execution, usage, (run,) = accounting(tmp_path)
    execution.started("harbor", run.id)
    usage.observe("harbor", "run", run.id, event(run))
    usage.start("harbor", "run", run.id, run.id)  # Completion reminder, same attempt.
    usage.end("harbor", "run", run.id, confirmed=True)
    execution.finish("harbor", run.id, "stopped")
    assert summary(execution).totals.partial == 1  # No second final result yet.
    usage.observe("harbor", "run", run.id, event(run, 200, identity="late-final"))
    assert execution.get("harbor", run.id).status == "stopped"
    assert summary(execution).totals.tokens.total == 360
    assert summary(execution).totals.attempts == 1
    assert summary(execution).totals.complete == 1
    usage.observe(
        "harbor",
        "run",
        run.id,
        event(run, 250, identity="failed-positive", final=False, gap="native-error"),
    )
    assert summary(execution).totals.tokens.total == 450
    usage.observe(
        "harbor",
        "run",
        run.id,
        event(run, 0, identity="crash-zero", final=False, gap="native-error"),
    )
    assert summary(execution).totals.tokens.total == 450
    assert summary(execution).totals.partial == 1


def test_receipt_buckets_conserve_late_deltas_and_execution_time(tmp_path, monkeypatch):
    execution, usage, (run,) = accounting(tmp_path)
    clock = ["2026-10-27T10:50:00+00:00"]
    monkeypatch.setattr("flowfield.application.now", lambda: clock[0])
    with execution.workspace.connection(write=True) as db:
        db.execute("UPDATE usage_executions SET started_at=?", (clock[0],))
    clock[0] = "2026-10-27T11:10:00+00:00"
    usage.observe("harbor", "run", run.id, event(run, 1000))
    usage.end("harbor", "run", run.id, confirmed=True)
    clock[0] = "2026-10-27T12:10:00+00:00"
    usage.observe("harbor", "run", run.id, event(run, 1000))
    usage.observe("harbor", "run", run.id, event(run, 1100, identity="late"))
    for zone in ("UTC", "America/Denver", "Asia/Kathmandu"):
        report = summary(
            execution,
            start="2026-10-27T00:00:00Z",
            end="2026-10-28T00:00:00Z",
            timezone=zone,
            group="hour",
        )
        assert sum(b.tokens.total or 0 for b in report.buckets) == 1980
        assert sum(b.recorded_seconds for b in report.buckets) == 1200
        assert report.totals.recorded_seconds == 1200
    report = summary(
        execution, start="2026-10-27T00:00:00Z", end="2026-10-28T00:00:00Z", group="hour"
    )
    assert report.buckets[10].tokens.total is None
    earlier = summary(execution, start="2026-10-27T10:00:00Z", end="2026-10-27T11:00:00Z")
    assert earlier.totals.tokens.total is None
    assert earlier.totals.coverage == "unavailable"
    assert earlier.totals.recorded_seconds == 600
    assert report.buckets[11].tokens.total == 1800
    assert report.buckets[12].tokens.total == 180
    assert report.buckets[10].recorded_seconds == report.buckets[11].recorded_seconds == 600


def test_dst_fall_and_spring_day_boundaries():
    for start, end, hours in (
        ("2026-11-01T06:00:00Z", "2026-11-02T07:00:00Z", 25),
        ("2026-03-08T07:00:00Z", "2026-03-09T06:00:00Z", 23),
    ):
        options = UsageFilter(start=start, end=end, timezone="America/Denver", group="hour")
        points = buckets(options)
        assert len(points) == hours
        day = buckets(options.model_copy(update={"group": "day"}))
        assert len(day) == 1
        assert (
            datetime.fromisoformat(day[0].end) - datetime.fromisoformat(day[0].start)
        ).total_seconds() == hours * 3600
    points = buckets(
        UsageFilter(
            start="2026-11-01T06:00:00Z",
            end="2026-11-02T07:00:00Z",
            timezone="America/Denver",
            group="hour",
        )
    )
    assert "01:00:00-06:00" in points[1].label
    assert "01:00:00-07:00" in points[2].label


def test_scope_unknown_end_retention_and_deletion(tmp_path, monkeypatch):
    execution, usage, (run,) = accounting(tmp_path)
    with pytest.raises(ApplicationError):
        usage.observe("other-workspace", "run", run.id, event(run))
    with pytest.raises(ApplicationError):
        usage.observe("harbor", "run", run.id, event(run, session_id="another-session"))
    monkeypatch.setattr("flowfield.usage.MAX_OBSERVATIONS", 2)
    for index in range(4):
        usage.observe("harbor", "run", run.id, event(run, 100 * (index + 1), identity=str(index)))
    usage.recover()
    report = summary(execution)
    assert report.totals.tokens.total == 360
    assert report.totals.unknown_intervals == 1 and report.totals.live_intervals == 0
    assert "evidence-retention-limit" in report.items[0].reasons
    assert report.totals.recorded_seconds is None
    with execution.workspace.connection(write=True) as db:
        db.execute("DELETE FROM runs WHERE id=?", (run.id,))
        assert db.execute("SELECT count(*) FROM usage_observations").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM usage_checkpoints").fetchone()[0] == 0
    assert summary(execution).totals.tokens.total is None


def test_read_pagination_and_filter_bounds(tmp_path):
    execution, usage, runs = accounting(tmp_path, count=3)
    for run in runs:
        usage.observe("harbor", "run", run.id, event(run))
    first = summary(execution, limit=1)
    second = summary(execution, limit=1, after=first.next_after)
    assert first.items[0].source_id != second.items[0].source_id
    assert first.totals.tokens.total == second.totals.tokens.total == 540
    assert summary(execution, model="no-such-model").totals.attempts == 0
    assert summary(execution, unknown_model=True).totals.attempts == 0
    for params in (
        {"limit": 101},
        {"timezone": "Unknown"},
        {"start": "2026-01-01T00:00:00Z"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "group": "hour"},
    ):
        with pytest.raises(ValidationError):
            UsageFilter(**params)


@pytest.mark.parametrize("confirmed", [True, False])
@pytest.mark.parametrize(
    "contract,version,semantics,per_turn",
    [
        ("claude-model-usage/0.3.293", "2.1.293", "disjoint-cache", 180),
        ("codex-thread-total/0.164.0", "0.164.0", "inclusive-cache", 120),
    ],
)
def test_session_resume_requires_a_reconciled_owner_boundary(
    tmp_path, confirmed, contract, version, semantics, per_turn
):

    execution, usage, (run,) = accounting(tmp_path)
    # Coordinator turns share a native session but never inherit an unrelated task.
    with execution.workspace.connection(write=True) as db:
        original = dict(db.execute("SELECT * FROM usage_executions").fetchone())
        db.execute(
            "UPDATE usage_executions SET source='coordinator_turn',task_id=NULL WHERE number=?",
            (original["number"],),
        )
        fields = [k for k in original if k != "number"]
        original.update(
            source="coordinator_turn",
            source_id="next-turn",
            task_id=None,
            session_id=None,
            started_at=None,
            dispatch_count=0,
        )
        db.execute(
            f"INSERT INTO usage_executions({','.join(fields)}) "
            f"VALUES({','.join('?' for _ in fields)})",
            [original[k] for k in fields],
        )
    usage.observe(
        "harbor",
        "coordinator_turn",
        run.id,
        event(run, contract=contract, native_version=version, semantics=semantics),
    )
    usage.end("harbor", "coordinator_turn", run.id, confirmed=confirmed)
    # A restart/native upgrade must preserve the original contract's checkpoint.
    usage = Usage(Workspace(execution.workspace.directory))
    usage.rebuild()
    usage.start("harbor", "coordinator_turn", "next-turn", run.id)
    usage.observe(
        "harbor",
        "coordinator_turn",
        "next-turn",
        event(
            run,
            200,
            identity="resumed-result",
            baseline="resume",
            contract=contract,
            native_version="9.0.0",
            semantics=semantics,
        ),
    )
    detail = UsageReads(execution.workspace).evidence("harbor", "coordinator_turn", "next-turn")
    assert detail.execution.tokens.total == (per_turn if confirmed else None)
    assert detail.items[0].baseline.total == per_turn
    assert summary(execution).totals.tokens.total == (2 * per_turn if confirmed else per_turn)


def test_reordered_without_source_order_is_explicit_gap(tmp_path):
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe("harbor", "run", run.id, event(run, 200))
    usage.observe("harbor", "run", run.id, event(run, 100, identity="older"))
    usage.end("harbor", "run", run.id, confirmed=True)
    report = summary(execution)
    assert report.totals.tokens.total == 360
    assert report.totals.partial == 1
    usage.rebuild()
    usage.observe("harbor", "run", run.id, event(run, 500, identity="after-unknown-reset"))
    assert summary(execution).totals.tokens.total == 360
    assert "unknown-reset-or-regression" in report.items[0].reasons


def test_archive_preserves_accounting_and_evidence(tmp_path):
    from flowfield.application import TaskEdit

    execution, usage, (run,) = accounting(tmp_path)
    usage.observe("harbor", "run", run.id, event(run))
    usage.end("harbor", "run", run.id, confirmed=True)
    execution.finish("harbor", run.id, "stopped")
    task = execution.workspace.task("harbor", run.task_id)
    execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, archived=True)
    )
    assert summary(execution).totals.tokens.total == 180
    assert (
        UsageReads(execution.workspace).evidence("harbor", "run", run.id).execution.tokens.total
        == 180
    )
    task = execution.workspace.task("harbor", run.task_id)
    execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, archived=False)
    )
    assert summary(execution).totals.tokens.total == 180


def test_invalid_numeric_overflow_stays_evidence_instead_of_breaking_capture(tmp_path):
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe(
        "harbor",
        "run",
        run.id,
        event(
            run, counters=Tokens(input=9_007_199_254_740_991, output=1, cache_read=1, cache_write=1)
        ),
    )
    evidence = UsageReads(execution.workspace).evidence("harbor", "run", run.id)
    assert evidence.items[0].disposition == "counter-overflow"
    assert evidence.execution.tokens.total is None


def test_deleted_scope_cannot_reenter_totals_after_rebuild_or_resume(tmp_path):
    execution, usage, (old, new) = accounting(tmp_path, count=2)
    usage.observe("harbor", "run", old.id, event(old))
    with execution.workspace.connection(write=True) as db:
        db.execute("DELETE FROM runs WHERE id=?", (old.id,))
        # Existing Coordinator continuity can reference a deleted native scope.
        db.execute("UPDATE usage_executions SET session_id=? WHERE source_id=?", (old.id, new.id))
    usage.rebuild()
    usage.observe("harbor", "run", new.id, event(old, 200, identity="resume", baseline="resume"))
    detail = UsageReads(execution.workspace).evidence("harbor", "run", new.id)
    assert detail.execution.tokens.total is None
    assert detail.items[0].disposition == "deleted-scope-boundary"


def test_published_task_deletion_removes_accounting_atomically(tmp_path):
    from flowfield.application import Edit
    from flowfield.task_deletion import TaskDeletionService

    execution, usage, (run,) = accounting(tmp_path)
    execution.started("harbor", run.id)
    usage.observe("harbor", "run", run.id, event(run))
    usage.end("harbor", "run", run.id, confirmed=True)
    execution.finish("harbor", run.id, "stopped")
    task = execution.workspace.task("harbor", run.task_id)
    TaskDeletionService(execution.workspace).delete(
        "harbor", task.id, Edit(expected_revision=task.revision)
    )
    assert summary(execution).totals.attempts == 0
    with execution.workspace.connection() as db:
        assert not db.execute("SELECT 1 FROM usage_observations").fetchone()
        assert not db.execute("SELECT 1 FROM usage_checkpoints").fetchone()
        assert db.execute("SELECT count(*) FROM usage_closed_scopes").fetchone()[0] == 1
        assert not db.execute("PRAGMA foreign_key_check").fetchone()


def test_elapsed_time_merges_parallel_work_and_clips_periods(tmp_path):
    execution, _, runs = accounting(tmp_path, count=3)
    with execution.workspace.connection(write=True) as db:
        for run, start, end in zip(runs, [0, 5, 30], [10, 20, 35], strict=True):
            db.execute(
                "UPDATE usage_executions SET started_at=?,ended_at=?,timing='recorded' "
                "WHERE source_id=?",
                (
                    f"2026-10-27T10:{start:02d}:00+00:00",
                    f"2026-10-27T10:{end:02d}:00+00:00",
                    run.id,
                ),
            )
    report = summary(execution)
    assert report.totals.recorded_seconds == 30 * 60
    assert report.totals.elapsed_seconds == 25 * 60
    assert next(r for r in report.breakdowns if r.dimension == "role").elapsed_seconds == 25 * 60
    # Pagination changes items, not the wall clock; idle gaps add nothing.
    assert summary(execution, limit=1, after=1).totals.elapsed_seconds == 25 * 60
    clipped = summary(execution, start="2026-10-27T10:08:00Z", end="2026-10-27T10:32:00Z")
    assert clipped.totals.elapsed_seconds == 14 * 60


def test_elapsed_time_includes_live_once_and_never_extends_unknown_end(tmp_path):
    from datetime import UTC, datetime, timedelta

    execution, _, runs = accounting(tmp_path, count=3)
    start = datetime.now(UTC) - timedelta(seconds=60)
    with execution.workspace.connection(write=True) as db:
        db.execute(
            "UPDATE usage_executions SET started_at=?,ended_at=NULL,timing='live'",
            (start.isoformat(),),
        )
        db.execute(
            "UPDATE usage_executions SET timing='unknown-end',started_at=? WHERE source_id=?",
            ((start - timedelta(hours=1)).isoformat(), runs[2].id),
        )
    report = summary(execution)
    assert 60 <= report.totals.elapsed_seconds < 65
    assert report.totals.recorded_seconds is None
    assert report.totals.live_intervals == 2
    assert report.totals.unknown_intervals == 1
    assert next(r for r in report.breakdowns if r.dimension == "role").live
    past = summary(
        execution,
        start=(start - timedelta(minutes=5)).isoformat(),
        end=(start - timedelta(seconds=1)).isoformat(),
    )
    assert past.totals.elapsed_seconds is None
    assert not any(r.live for r in past.breakdowns)


def test_corrections_count_started_work_without_retries_or_page_bias(tmp_path):
    execution, _, runs = accounting(tmp_path, count=5)
    with execution.workspace.connection(write=True) as db:
        for run, purpose in zip(
            runs,
            ["correction", "retry", "answer-continuation", "implementation", "correction"],
            strict=True,
        ):
            db.execute(
                "UPDATE usage_executions SET purpose=?,started_at=?,ended_at=?,timing='recorded' "
                "WHERE source_id=?",
                (purpose, "2026-10-27T10:00:00+00:00", "2026-10-27T10:20:00+00:00", run.id),
            )
        db.execute("UPDATE usage_executions SET started_at=NULL WHERE source_id=?", (runs[-1].id,))
    assert summary(execution, limit=1, after=1).totals.corrections == 1
    assert summary(execution, limit=1, after=1).totals.retries == 1
    assert summary(execution, task_id=runs[0].task_id).totals.corrections == 1
    assert summary(execution, task_id=runs[1].task_id).totals.corrections == 0
    # A run overlapping a period is not another correction started in that period.
    assert (
        summary(
            execution, start="2026-10-27T10:05:00Z", end="2026-10-27T10:15:00Z"
        ).totals.corrections
        == 0
    )
    with execution.workspace.connection(write=True) as db:
        db.execute("UPDATE usage_executions SET purpose='unknown' WHERE source_id=?", (runs[0].id,))
    assert summary(execution).totals.corrections is None


def test_task_metrics_aggregate_full_selection_and_union_parallel_time(tmp_path):
    execution, usage, runs = accounting(tmp_path, count=3)
    for run in runs:
        usage.observe("harbor", "run", run.id, event(run))
    with execution.workspace.connection(write=True) as db:
        for run, start, end in zip(runs, [0, 5, 10], [10, 20, 30], strict=True):
            db.execute(
                "UPDATE usage_executions SET task_id=?,started_at=?,ended_at=?,timing='recorded' "
                "WHERE source_id=?",
                (
                    runs[0].task_id,
                    f"2026-10-27T10:{start:02d}:00+00:00",
                    f"2026-10-27T10:{end:02d}:00+00:00",
                    run.id,
                ),
            )
        db.execute("UPDATE usage_observations SET received_at='2026-10-27T10:12:00+00:00'")
    report = summary(
        execution, limit=1, after=1, start="2026-10-27T10:08:00Z", end="2026-10-27T10:25:00Z"
    )
    assert len(report.items) == 1
    assert len(report.tasks) == 1
    row = report.tasks[0]
    assert row.task_id == runs[0].task_id
    assert row.key == execution.workspace.task("harbor", row.task_id).key
    assert row.tokens.total == 540
    assert row.elapsed_seconds == 17 * 60
    assert row.elapsed_seconds == report.totals.elapsed_seconds
    assert summary(execution, role="coordinator").tasks == []
    # Filtering is applied before task aggregation, with unknown totals preserved.
    with execution.workspace.connection(write=True) as db:
        db.execute("DELETE FROM usage_observations")
    unknown = summary(execution).tasks[0]
    assert unknown.tokens.total is None
