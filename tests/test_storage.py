"""Fresh initialization, strict schema boundaries and current-state recovery guards."""

import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing

import pytest
from storage_fixtures import logical_data, raw
from typer.testing import CliRunner

from flowfield import storage
from flowfield.application import Workspace
from flowfield.cli import app
from flowfield.errors import ApplicationError
from flowfield.schema import SCHEMA, SCHEMA_VERSION


def test_fresh_schema_has_one_owner_and_reopens_without_rewriting(tmp_path):
    directory = tmp_path / "state"
    store = Workspace(directory)
    with raw(directory) as db:
        assert storage.version(db) == SCHEMA_VERSION
        assert storage.identity(db)[1] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone()
            is None
        )
        assert db.execute("SELECT harness FROM harness_settings ORDER BY harness").fetchall() == [
            ("claude-code",),
            ("codex",),
            ("pi",),
        ]
    before = logical_data(directory)
    assert store.projects() == []
    assert Workspace(directory).projects() == []
    assert logical_data(directory) == before
    assert not (directory / "backups").exists()


@pytest.mark.parametrize("version", [0, 44, 81, SCHEMA_VERSION + 1])
def test_incompatible_state_is_rejected_without_changes(tmp_path, version):
    database = tmp_path / storage.DATABASE
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE old_work (description TEXT)")
        db.execute("INSERT INTO old_work VALUES ('Do not silently delete data')")
        db.execute(f"PRAGMA user_version={version}")
    before = database.read_bytes()
    with pytest.raises(ApplicationError, match="Preserve this data directory"):
        Workspace(tmp_path)
    assert database.read_bytes() == before
    assert not (tmp_path / "artifacts").exists()
    assert not (tmp_path / "backups").exists()
    assert storage.status(tmp_path)["schema_version"] == version


def test_initialization_failure_rolls_back_ddl_and_version(tmp_path):
    broken = SCHEMA + "CREATE TABLE partial (id INTEGER); INSERT INTO missing VALUES (1);"
    with pytest.raises(sqlite3.OperationalError, match="missing"):
        storage.initialize(tmp_path, broken)
    with raw(tmp_path) as db:
        assert storage.version(db) == 0
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
    Workspace(tmp_path)
    assert storage.status(tmp_path)["schema_version"] == SCHEMA_VERSION


def test_invalid_fresh_references_roll_back_before_commit(tmp_path):
    broken = SCHEMA + "INSERT INTO workspace_projects VALUES ('missing', 'missing');"
    with pytest.raises(sqlite3.IntegrityError):
        storage.initialize(tmp_path, broken)
    with raw(tmp_path) as db:
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
    Workspace(tmp_path)


def test_killed_initialization_releases_ownership_and_rolls_back(tmp_path):
    marker = tmp_path / "inside-transaction"
    program = """
import sys, time
from pathlib import Path
from flowfield import storage
from flowfield.schema import SCHEMA

directory = Path(sys.argv[1])
marker = Path(sys.argv[2])
connect = storage.connect
def pause():
    marker.touch()
    time.sleep(30)
def instrumented(*args, **kwargs):
    db = connect(*args, **kwargs)
    db.create_function('pause_initialization', 0, pause)
    return db
storage.connect = instrumented
storage.initialize(directory, SCHEMA + 'SELECT pause_initialization();')
"""
    state = tmp_path / "state"
    process = subprocess.Popen([sys.executable, "-c", program, str(state), str(marker)])
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.01)
        assert marker.exists(), "Initializer did not reach its open transaction"
        with pytest.raises(ApplicationError, match="Another Flowfield"):
            storage.acquire_lock(state, ".execution.lock")
    finally:
        process.kill()
        process.wait(timeout=5)
    with raw(state) as db:
        assert storage.version(db) == 0
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
    assert Workspace(state).projects() == []


def test_active_execution_owner_allows_current_readers_only(tmp_path):
    store = Workspace(tmp_path)
    with storage.acquire_lock(tmp_path, ".execution.lock"):
        assert Workspace(tmp_path).projects() == []
        with pytest.raises(ApplicationError, match="Another Flowfield"):
            storage.acquire_lock(tmp_path, ".execution.lock")
        with raw(tmp_path) as db:
            db.execute("PRAGMA user_version=81")
        with pytest.raises(ApplicationError, match="Preserve this data directory"):
            Workspace(tmp_path)
    with pytest.raises(ApplicationError, match="schema is 81"):
        store.projects()


def test_initialization_lock_prevents_overlapping_reads_and_writes(tmp_path):
    store = Workspace(tmp_path)
    with storage.acquire_lock(tmp_path, ".initialize.lock"):
        with pytest.raises(ApplicationError, match="Another Flowfield"):
            Workspace(tmp_path)
        with pytest.raises(ApplicationError, match="Another Flowfield"):
            store.projects()
    assert store.projects() == []


def test_database_symlink_is_rejected(tmp_path):
    real = tmp_path / "real"
    Workspace(real)
    state = tmp_path / "linked"
    state.mkdir()
    (state / storage.DATABASE).symlink_to(real / storage.DATABASE)
    before = logical_data(real)
    with pytest.raises(ApplicationError, match="symlink"):
        Workspace(state)
    assert logical_data(real) == before


def test_offline_status_never_initializes_and_retired_commands_are_removed(tmp_path):
    directory = tmp_path / "state"
    runner = CliRunner()
    args = ["--data-dir", str(directory), "storage"]
    result = runner.invoke(app, [*args, "status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "database": str(directory / storage.DATABASE),
        "schema_version": None,
        "supported_version": SCHEMA_VERSION,
    }
    assert not directory.exists()
    for command in ("backups", "restore"):
        assert runner.invoke(app, [*args, command]).exit_code != 0
    assert not directory.exists()
    Workspace(directory)
    with closing(sqlite3.connect(directory / storage.DATABASE)) as db:
        db.execute("PRAGMA user_version=81")
    before = (directory / storage.DATABASE).read_bytes()
    result = runner.invoke(app, [*args, "status"])
    assert result.exit_code == 0
    assert "preserve this directory" in result.stdout
    assert (directory / storage.DATABASE).read_bytes() == before
