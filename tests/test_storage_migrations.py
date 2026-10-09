"""Upgrade/recovery journeys with persisted work and process failures; no live models."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from threading import Event

import pytest
from test_execution import BASE, RESULT
from test_execution import fixture as execution_fixture
from test_input_continuation import answer, question, queue
from test_results import approve, current
from test_results import fixture as result_fixture
from typer.testing import CliRunner

from flowfield import migrations, storage
from flowfield.activity import ActivityCreate
from flowfield.application import SCHEMA, TaskEdit, Workspace
from flowfield.cli import app
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.migrations import Migration
from flowfield.questions import Questions
from flowfield.supervisor import Supervisor

CURRENT = migrations.current_version()


def raw(directory):
    return closing(storage.connect(directory / storage.DATABASE))


def add_column(db):
    db.execute("ALTER TABLE tasks ADD COLUMN migration_note TEXT NOT NULL DEFAULT ''")


def later(monkeypatch, *extra):
    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS, *extra))


def logical_data(directory):
    with raw(directory) as db:
        return list(db.iterdump())


def test_frozen_baseline_and_fresh_initialization(tmp_path):
    frozen = Path(__file__).with_name("fixtures") / "schema_44.sql"
    assert SCHEMA == frozen.read_text()  # Editing baseline would invalidate existing upgrades.
    workspace = Workspace(tmp_path / "state")
    with raw(workspace.directory) as db:
        assert storage.version(db) == CURRENT
        assert storage.identity(db)[1] == 0
        assert db.execute("SELECT version, backup FROM schema_migrations").fetchall() == [
            *((migration.version, None) for migration in migrations.MIGRATIONS)
        ]
    assert storage.backups(workspace.directory) == []
    assert Workspace(workspace.directory).projects() == []


def test_current_workspace_reopens_without_rewriting_records(tmp_path):
    workspace = execution_fixture(tmp_path).workspace
    workspace.add_activity("harbor", ActivityCreate(task_id="task-0", body="Keep this observation"))
    with raw(workspace.directory) as db:
        db.execute("INSERT INTO schema_migrations VALUES (44, '2026-10-06', '0.1.0', NULL)")
    before = logical_data(workspace.directory)
    for _ in range(2):
        Workspace(workspace.directory)
        assert logical_data(workspace.directory) == before
    assert storage.backups(workspace.directory) == []


def test_baseline_upgrade_accepts_ddl_spacing_but_not_changed_constraints(tmp_path, monkeypatch):
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    for name, kinds, supported in (
        ("equivalent", "'note',  'handoff','event'", True),
        ("different", "'note','event'", False),
    ):
        directory = tmp_path / name
        directory.mkdir()
        with sqlite3.connect(directory / storage.DATABASE) as db:
            db.executescript(SCHEMA.replace("'note', 'handoff', 'event'", kinds))
        before = logical_data(directory)
        if supported:
            assert Workspace(directory).schema_version == CURRENT + 1
        else:
            with pytest.raises(ApplicationError, match="does not match"):
                Workspace(directory)
            assert logical_data(directory) == before


def test_baseline_upgrade_preserves_approved_result_and_real_git_delivery(tmp_path, monkeypatch):
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "MIGRATIONS", ())
        service, repo, run = result_fixture(tmp_path)
        service.results.process("harbor")
        ready = current(service)
        approve(service, ready)
        saved_result = current(service)
        service.workspace.add_activity(
            "harbor", ActivityCreate(task_id="work", body="Keep this task-feed evidence")
        )
        activity = service.workspace.activity("harbor", task_id="work")
        assignment = service.execution.assignment("harbor", run.id)
        database_before = logical_data(service.workspace.directory)
    directory = service.workspace.directory
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    upgraded = Supervisor(Workspace(directory))
    assert current(upgraded) == saved_result
    assert upgraded.workspace.activity("harbor", task_id="work") == activity
    assert upgraded.execution.assignment("harbor", run.id) == assignment
    backup = storage.backups(directory)[0]
    assert backup["schema_version"] == migrations.BASELINE_VERSION
    with closing(
        storage.connect(storage.backup_path(directory, backup["id"]) / storage.DATABASE)
    ) as db:
        assert list(db.iterdump()) == database_before
    # Migration didn't rewrite project identity, worktrees or result bindings.
    assert (repo / ".flowfield/config.toml").exists()
    upgraded.execution.restart()
    assert not upgraded.execution.settings("harbor").enabled
    upgraded.integrations.restart()
    upgraded.results.process("harbor")
    assert current(upgraded).status == "delivered"
    assert upgraded.workspace.task("harbor", "work").status == "done"
    assert (repo / "result.txt").read_text() == "implemented\n"
    upgraded.results.process("harbor")
    assert current(upgraded).status == "delivered"
    assert len(storage.backups(directory)) == 1


def test_answer_and_interrupted_run_survive_upgrade_and_continue_once(tmp_path, monkeypatch):
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "MIGRATIONS", ())
        execution = execution_fixture(tmp_path, count=2, cap=2)
        run, q = question(execution)
        independent = execution.claim("harbor", BASE, {BASE: set()})
        execution.started("harbor", independent.id)
        saved_answer = answer(Questions(execution.workspace), q)
        execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    recovered = Execution(Workspace(execution.workspace.directory))
    recovered.restart()
    assert not recovered.settings("harbor").enabled
    assert recovered.get("harbor", independent.id).status == "uncertain"
    assert Questions(recovered.workspace).get("harbor", q.id).answer == saved_answer.answer
    assert recovered.claim("harbor", BASE, {RESULT: set(), BASE: set()}) is None
    queue(recovered, True)
    successor = recovered.claim("harbor", BASE, {RESULT: set(), BASE: set()})
    assert successor.predecessor_id == run.id and successor.input_base_commit == RESULT
    assert (
        json.loads(recovered.assignment("harbor", successor.id)["input"])[0]["answer"]
        == saved_answer.answer
    )
    assert recovered.claim("harbor", BASE, {RESULT: set(), BASE: set()}) is None


def test_multi_step_failure_rolls_back_data_ddl_versions_and_bounds_backups(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "state")
    before = logical_data(workspace.directory)

    def fail(db):
        db.execute("CREATE TABLE should_rollback (value TEXT)")
        db.execute("INSERT INTO should_rollback VALUES ('partial')")
        raise ValueError("injected migration failure")

    later(monkeypatch, Migration(CURRENT + 1, add_column), Migration(CURRENT + 2, fail))
    for _ in range(5):
        with pytest.raises(ApplicationError, match="rolled back.*injected") as error:
            Workspace(workspace.directory)
        assert "storage restore" in str(error.value)
        assert logical_data(workspace.directory) == before
    assert len(storage.backups(workspace.directory)) == storage.BACKUP_LIMIT
    with raw(workspace.directory) as db:
        assert storage.version(db) == CURRENT
        assert db.execute("SELECT version FROM schema_migrations").fetchall() == [
            (migration.version,)
            for migration in migrations.MIGRATIONS
            if migration.version <= CURRENT
        ]


def test_backup_failure_prevents_any_upgrade(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "state")
    before = logical_data(workspace.directory)
    later(monkeypatch, Migration(CURRENT + 1, add_column))

    def unavailable(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "copy_database", unavailable)
    with pytest.raises(ApplicationError, match="disk full"):
        Workspace(workspace.directory)
    assert logical_data(workspace.directory) == before
    assert list((workspace.directory / "backups").iterdir()) == []


def test_migration_cannot_commit_outside_runner_transaction(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "state")
    before = logical_data(workspace.directory)

    def bad(db):
        db.execute("CREATE TABLE should_rollback (value TEXT)")
        db.commit()

    later(monkeypatch, Migration(CURRENT + 1, bad))
    with pytest.raises(ApplicationError, match="not authorized"):
        Workspace(workspace.directory)
    assert logical_data(workspace.directory) == before


def test_killed_process_rolls_back_and_releases_ownership(tmp_path):
    workspace = Workspace(tmp_path / "state")
    before = logical_data(workspace.directory)
    ready = tmp_path / "ready"
    code = """
import sys, time
from pathlib import Path
from flowfield import migrations
from flowfield.application import Workspace
def interrupted(db):
    db.execute('CREATE TABLE interrupted (value TEXT)')
    Path(sys.argv[2]).write_text('ready')
    time.sleep(60)
migrations.MIGRATIONS += (migrations.Migration(migrations.current_version() + 1, interrupted),)
Workspace(Path(sys.argv[1]))
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(workspace.directory), str(ready)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        with pytest.raises(ApplicationError, match="owns this workspace"):
            Workspace(workspace.directory)
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert Workspace(workspace.directory).projects() == []
    assert logical_data(workspace.directory) == before
    assert len(storage.backups(workspace.directory)) == 1


def test_active_service_and_open_transactions_exclude_maintenance(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "state")
    with storage.acquire_lock(workspace.directory, ".execution.lock"):
        assert Workspace(workspace.directory).projects() == []
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    with storage.acquire_lock(workspace.directory, ".execution.lock"):
        with pytest.raises(ApplicationError, match="owns this workspace"):
            Workspace(workspace.directory)
    with workspace.connection():
        with pytest.raises(ApplicationError, match="owns this workspace"):
            Workspace(workspace.directory)
    assert storage.backups(workspace.directory) == []
    assert Workspace(workspace.directory).schema_version == CURRENT + 1
    with pytest.raises(ApplicationError, match="Reopen"):
        workspace.projects()


def test_upgrade_between_construction_and_service_start_is_refused(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "state")
    service = Supervisor(workspace)
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    Workspace(workspace.directory)
    with pytest.raises(ApplicationError, match="Reopen"):
        asyncio.run(service.start())
    assert service.lock is None and service.loop_task is None
    with storage.maintenance(workspace.directory):
        pass


@pytest.mark.parametrize("schema_version", [0, 29, 43, 999])
def test_unversioned_nonempty_and_newer_databases_are_unchanged(tmp_path, schema_version):
    with sqlite3.connect(tmp_path / storage.DATABASE) as db:
        db.execute("CREATE TABLE saved (value TEXT)")
        db.execute("INSERT INTO saved VALUES ('keep')")
        db.execute(f"PRAGMA user_version={schema_version}")
    before = (tmp_path / storage.DATABASE).read_bytes()
    with pytest.raises(ApplicationError, match="preserved"):
        Workspace(tmp_path)
    assert (tmp_path / storage.DATABASE).read_bytes() == before
    assert storage.backups(tmp_path) == []


def test_wrong_baseline_is_not_adopted(tmp_path, monkeypatch):
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    with sqlite3.connect(tmp_path / storage.DATABASE) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
        db.execute(f"PRAGMA user_version={migrations.BASELINE_VERSION}")
    with pytest.raises(ApplicationError, match="does not match"):
        Workspace(tmp_path)
    assert storage.backups(tmp_path) == []


def test_recovery_preserves_artifacts_and_refuses_new_work(tmp_path, monkeypatch):
    execution = execution_fixture(tmp_path)
    workspace = execution.workspace
    artifact = workspace.directory / "artifacts" / "keep.txt"
    artifact.write_text("evidence")
    before = logical_data(workspace.directory)
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    upgraded = Workspace(workspace.directory)
    saved = storage.backups(workspace.directory)[0]["id"]
    with storage.acquire_lock(workspace.directory, ".execution.lock"):
        with pytest.raises(ApplicationError, match="owns this workspace"):
            storage.restore(workspace.directory, saved)
    recovered = storage.restore(workspace.directory, saved)
    assert recovered["safety_backup"] != saved
    assert logical_data(workspace.directory) == before
    assert artifact.read_text() == "evidence"
    upgraded = Workspace(workspace.directory)
    saved = storage.backups(workspace.directory)[0]["id"]
    task = upgraded.task("harbor", "task-0")
    upgraded.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, title="New work")
    )
    changed = logical_data(workspace.directory)
    with pytest.raises(ApplicationError, match="changed since"):
        storage.restore(workspace.directory, saved)
    assert logical_data(workspace.directory) == changed


def test_baseline_recovery_and_invalid_backup_guard(tmp_path, monkeypatch):
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "MIGRATIONS", ())
        workspace = Workspace(tmp_path / "state")
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    Workspace(workspace.directory)
    saved = storage.backups(workspace.directory)[0]["id"]
    with pytest.raises(ApplicationError, match="Choose a backup"):
        storage.restore(workspace.directory, "../workspace.sqlite3")
    storage.restore(workspace.directory, saved)
    assert storage.status(workspace.directory)["schema_version"] == migrations.BASELINE_VERSION
    Workspace(workspace.directory)
    newest = storage.backups(workspace.directory)[0]["id"]
    target = storage.backup_path(workspace.directory, newest) / storage.DATABASE
    target.write_bytes(b"broken backup")
    before = logical_data(workspace.directory)
    with pytest.raises(ApplicationError, match="checksum"):
        storage.restore(workspace.directory, newest)
    assert logical_data(workspace.directory) == before


def test_offline_cli_is_clean_and_never_initializes_state(tmp_path):
    runner = CliRunner()
    directory = tmp_path / "missing"
    args = ["--data-dir", str(directory), "storage"]
    result = runner.invoke(app, [*args, "status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["schema_version"] is None and result.stderr == ""
    assert not directory.exists()
    assert (
        runner.invoke(app, [*args, "backups"]).output.strip() == "No pre-upgrade database backups."
    )
    result = runner.invoke(app, [*args, "restore", "some-backup", "--json"])
    assert result.exit_code == 1 and result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "confirmation_required"
    assert not directory.exists()


def test_wal_snapshot_restore_and_cli_recovery(tmp_path, monkeypatch):
    execution = execution_fixture(tmp_path)
    directory = execution.workspace.directory
    with raw(directory) as keeper:
        assert keeper.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        execution.workspace.add_activity(
            "harbor", ActivityCreate(task_id="task-0", body="Committed in WAL")
        )
        before = logical_data(directory)
        later(monkeypatch, Migration(CURRENT + 1, add_column))
        Workspace(directory)
        saved = storage.backups(directory)[0]["id"]
        with closing(
            storage.connect(storage.backup_path(directory, saved) / storage.DATABASE)
        ) as db:
            assert list(db.iterdump()) == before
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    runner = CliRunner()
    result = runner.invoke(
        app, ["--data-dir", str(directory), "storage", "restore", saved, "--confirm", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert result.stderr == "" and json.loads(result.stdout)["restored"] == saved
    assert logical_data(directory) == before
    assert not Path(str(directory / storage.DATABASE) + "-wal").exists()


def test_foreign_workspace_and_newer_schema_recovery_are_refused(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "state")
    later(monkeypatch, Migration(CURRENT + 1, add_column))
    Workspace(workspace.directory)
    name = storage.backups(workspace.directory)[0]["id"]
    with raw(workspace.directory) as db:
        db.execute("UPDATE storage_metadata SET workspace_id='another-workspace'")
    with pytest.raises(ApplicationError, match="another workspace"):
        storage.restore(workspace.directory, name)
    with raw(workspace.directory) as db:
        db.execute("PRAGMA user_version=999")
    before = (workspace.directory / storage.DATABASE).read_bytes()
    with pytest.raises(ApplicationError, match="newer Flowfield"):
        storage.restore(workspace.directory, name)
    assert (workspace.directory / storage.DATABASE).read_bytes() == before


def test_abandoned_copies_are_cleaned_without_touching_other_files(tmp_path):
    workspace = Workspace(tmp_path / "state")
    root = workspace.directory / "backups"
    root.mkdir()
    pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=root))
    (pending / storage.DATABASE).write_bytes(b"incomplete")
    restore = workspace.directory / (".restore-" + "a" * 32 + ".sqlite3")
    restore.write_bytes(b"incomplete")
    unrelated = root / "personal-copy.sqlite3"
    unrelated.write_bytes(b"keep")
    Workspace(workspace.directory)
    assert not pending.exists() and not restore.exists()
    assert unrelated.read_bytes() == b"keep"


def test_cancelled_restart_keeps_ownership_until_its_thread_exits(tmp_path, monkeypatch):
    service = Supervisor(Workspace(tmp_path / "state"))
    entered, release = Event(), Event()

    def recover():
        entered.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(service.integrations, "restart", recover)

    async def scenario():
        task = asyncio.create_task(service.start())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                with pytest.raises(ApplicationError, match="owns this workspace"):
                    with storage.maintenance(service.workspace.directory):
                        pass
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.lock is None and service.loop_task is None
        with storage.maintenance(service.workspace.directory):
            pass

    asyncio.run(scenario())
