"""Retiring allowances preserves work, recovery, grants and queue authority."""

import json

import pytest
from test_execution import BASE, fixture

from flowfield import migrations, storage
from flowfield.application import Workspace
from flowfield.attention import attention_page
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, RunAction


def legacy_state(tmp_path, monkeypatch, status):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 73)
        )
        execution = fixture(tmp_path)
        run = execution.claim("harbor", BASE, {BASE: set()})
        execution.started("harbor", run.id)
        if status == "stopped":
            execution.finish("harbor", run.id, "stopped", problem="Active allowance reached.")
        settings = execution.settings("harbor")
        execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
        with execution.workspace.connection(write=True) as db:
            db.execute(
                "INSERT INTO execution_policies VALUES ('harbor', ?)",
                (json.dumps({"revision": 3, "active_seconds": 1, "corrections": 0}),),
            )
            db.execute(
                "INSERT INTO task_execution_limits VALUES ('harbor',?,'active_time',?)",
                (run.task_id, json.dumps({"blocked": "active_time"})),
            )
            db.execute(
                "UPDATE runs SET data=json_set(data,'$.active_seconds',9,'$.active_since',100),"
                "assignment=json_set(assignment,'$.execution_limits','retired allowance')"
            )
            db.execute("INSERT INTO worker_check_grants VALUES ('harbor','{}')")
        return execution, run


@pytest.mark.parametrize("status", ["stopped", "running"])
def test_retirement_preserves_queue_work_and_recovery(tmp_path, monkeypatch, status):
    previous, run = legacy_state(tmp_path, monkeypatch, status)
    task = previous.workspace.task("harbor", run.task_id)
    settings = previous.settings("harbor")
    reopened = Workspace(previous.workspace.directory)
    execution = Execution(reopened)
    assert reopened.task("harbor", run.task_id) == task
    assert execution.settings("harbor") == settings
    assert execution.get("harbor", run.id).status == status
    assert "execution_limits" not in execution.assignment("harbor", run.id)
    with reopened.connection() as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN "
            "('task_execution_limits','execution_policies')"
        ).fetchall()
        assert db.execute("SELECT data FROM worker_check_grants").fetchone()[0] == "{}"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert all(
        i.kind != "execution_limit" for i in attention_page(reopened, "harbor", "action").items
    )
    assert storage.backups(reopened.directory)[0]["schema_version"] == 73
    if status == "running":
        execution.restart()
        assert execution.get("harbor", run.id).status == "uncertain"
    else:
        stopped = execution.get("harbor", run.id)
        execution.retry("harbor", run.id, RunAction(expected_revision=stopped.revision))
        assert execution.claim("harbor", BASE, {BASE: set()}) is None  # Still paused.
        execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
        assert execution.claim("harbor", BASE, {BASE: set()})


def test_retirement_failure_rolls_back_and_can_retry(tmp_path, monkeypatch):
    from flowfield.migrations import Migration
    from flowfield.migrations.v074_remove_execution_limits import apply

    previous, run = legacy_state(tmp_path, monkeypatch, "stopped")

    def interrupted(db):
        apply(db)
        raise RuntimeError("Interrupted allowance retirement")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            (
                *tuple(m for m in migrations.MIGRATIONS if m.version <= 73),
                Migration(74, interrupted),
            ),
        )
        with pytest.raises(Exception, match="Interrupted allowance retirement"):
            Workspace(previous.workspace.directory)
    with storage.connect(previous.workspace.database) as db:
        assert storage.version(db) == 73
        assert (
            db.execute("SELECT blocked FROM task_execution_limits").fetchone()[0] == "active_time"
        )
        assert json.loads(db.execute("SELECT data FROM runs").fetchone()[0])["active_seconds"] == 9
    execution = Execution(Workspace(previous.workspace.directory))
    assert execution.get("harbor", run.id).status == "stopped"
