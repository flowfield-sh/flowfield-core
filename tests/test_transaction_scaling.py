"""Bounded maintenance and transaction-local task reads preserve workflow evidence."""

import json
import sqlite3
from unittest.mock import patch

import pytest
from execution_fixtures import BASE, fixture, result
from workspace_fixtures import TEST_AGENTS

from flowfield import storage
from flowfield.agent_settings import AgentSettings
from flowfield.application import TaskCreate, TaskEdit, Workspace
from flowfield.errors import ApplicationError
from flowfield.reviewer_settings import Reviewers
from flowfield.schema import SCHEMA_VERSION
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_mutations_reuse_records_without_losing_bodies_or_dependency_changes(tmp_path, monkeypatch):
    execution = fixture(tmp_path)
    result(execution)
    store = execution.workspace
    task = store.create_task(
        "harbor",
        TaskCreate(
            title="Next",
            body="Preserve the entire assignment.",
            completion="report",
            status="up_next",
            dependencies=["T-1"],
        ),
    )
    original = store._records
    reads = []

    def records(db, owner):
        reads.append(owner)
        return original(db, owner)

    monkeypatch.setattr(store, "_records", records)
    changed = store.edit_task(
        "harbor",
        task.key.lower(),
        TaskEdit(
            expected_revision=task.revision,
            title="Renamed",
        ),
    )
    assert reads == ["harbor"]  # Regression budget: no whole-workspace rereads per mutation.
    assert changed.body == task.body and changed.prerequisites[0].key == "T-1"
    assert changed == store.task("harbor", task.id)
    reads.clear()
    run = execution.claim("harbor", BASE, {BASE: set()}, task_id=task.id)
    assert run and reads == ["harbor"]
    assert run.agreement_revision == changed.agreement_revision
    with store.connection() as db:
        assignment = json.loads(
            db.execute("SELECT assignment FROM runs WHERE id=?", (run.id,)).fetchone()[0]
        )
    assert assignment["description"] == task.body
    upstream = store.task("harbor", "T-1")
    with pytest.raises(ApplicationError, match="Done is permanent"):
        store.edit_task(
            "harbor",
            upstream.id,
            TaskEdit(
                expected_revision=upstream.revision,
                body="Changed upstream requirements.",
            ),
        )
    downstream = store.task("harbor", task.id)
    assert not downstream.reconciliation_reason
    assert downstream.body == task.body
    assert not downstream.blocked_by
    assert store.task("harbor", "T-1") == upstream


def test_settings_resolve_scoped_keys_without_reading_task_history(tmp_path, monkeypatch):
    store = fixture(tmp_path).workspace
    other = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Other"))
    store.create_task(other.id, TaskCreate(title="Other first task", id="other-task"))
    monkeypatch.setattr(
        store, "_task", lambda *_: pytest.fail("Settings must not load task history")
    )
    settings = AgentSettings(store)
    assert settings.get("harbor", "worker", "t-1") == settings.get("harbor", "worker", "task-0")
    assert Reviewers(store).get("harbor", entity="t-1") == Reviewers(store).get(
        "harbor", entity="task-0"
    )
    for reference in ("other-task", "missing"):
        with pytest.raises(ApplicationError) as error:
            settings.get("harbor", "worker", reference)
        assert error.value.code == "not_found"


def application_rows(store):
    with store.connection() as db:
        return {
            name: db.execute(f'SELECT * FROM "{name}"').fetchall()
            for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }


@pytest.mark.parametrize("legacy", [False, True])
def test_statistics_skip_busy_writer_then_preserve_all_application_rows(
    tmp_path, monkeypatch, legacy
):
    if legacy:
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 45, 0))
    store = fixture(tmp_path, count=20).workspace
    before = application_rows(store)
    with store.connection(write=True):
        assert not storage.optimize(store.directory, store.schema_version)
    assert storage.optimize(store.directory, store.schema_version)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0] > 0
    assert application_rows(store) == before


def test_statistics_budget_interrupts_sql_and_retries_cleanly(tmp_path, monkeypatch):
    store = fixture(tmp_path, count=20).workspace
    before = application_rows(store)
    ticks = iter([0.0])
    with monkeypatch.context() as changes:
        changes.setattr(storage, "monotonic", lambda: next(ticks, 1.0))
        assert not storage.optimize(store.directory, store.schema_version)
    assert application_rows(store) == before
    assert storage.optimize(store.directory, store.schema_version)
    assert application_rows(store) == before


def test_service_maintenance_is_throttled_and_retries_contention(tmp_path):
    service = Supervisor(fixture(tmp_path).workspace)
    with (
        patch("flowfield.supervisor.monotonic", return_value=100.0),
        patch(
            "flowfield.supervisor.optimize",
            return_value=False,
        ) as optimize,
    ):
        service.next_statistics = 0
        service._maintain()
        assert service.next_statistics == 160.0 and optimize.call_count == 1
        service._maintain()
        assert optimize.call_count == 1
    with (
        patch("flowfield.supervisor.monotonic", return_value=160.0),
        patch(
            "flowfield.supervisor.optimize",
            return_value=True,
        ) as optimize,
    ):
        service._maintain()
        assert service.next_statistics == 3760.0 and optimize.call_count == 1


@pytest.mark.parametrize("interrupted", [False, True])
def test_index_upgrade_preserves_95_records_and_does_not_reset_sessions(
    tmp_path, monkeypatch, interrupted
):
    store = fixture(tmp_path).workspace
    with store.connection(write=True, notify=False) as db:
        db.execute(
            "INSERT INTO coordinator_session_generations VALUES (?,?,?)",
            (
                "retained",
                "harbor",
                json.dumps({"harness": "claude-code"}),
            ),
        )
        db.execute("INSERT INTO coordinator_sessions VALUES (?,?,0)", ("harbor", "retained"))
        db.execute("DROP INDEX result_run")
        db.execute("PRAGMA user_version=95")
    # The prior object intentionally cannot write after a schema change.
    store.schema_version = 95
    before = application_rows(store)
    if interrupted:
        original = storage.integrity

        def fail(db):
            original(db)
            if storage.version(db) == SCHEMA_VERSION:
                raise RuntimeError("interrupted index upgrade")

        with monkeypatch.context() as changes:
            changes.setattr(storage, "integrity", fail)
            with pytest.raises(RuntimeError, match="interrupted index"):
                Workspace(store.directory)
        assert application_rows(store) == before
    upgraded = Workspace(store.directory)
    assert application_rows(upgraded) == before
    with upgraded.connection() as db:
        assert db.execute("SELECT fresh FROM coordinator_sessions").fetchone()[0] == 0
        assert [r[2] for r in db.execute("PRAGMA index_info(result_run)")] == ["run_id"]
    backups = list((store.directory / "backups").glob("schema-95-*.sqlite3"))
    assert backups
    with sqlite3.connect(backups[-1]) as db:
        assert storage.version(db) == 95
        assert not db.execute("PRAGMA index_info(result_run)").fetchall()
