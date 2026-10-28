"""Supported published upgrade preserves identities/results and survives interruption."""

import json
import sqlite3
from contextlib import closing

import pytest
from usage_fixtures import accounting

from flowfield import storage, usage_schema
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.usage_models import UsageFilter
from flowfield.usage_reads import UsageReads


def legacy_store(tmp_path, version=89):
    execution, _, (run,) = accounting(tmp_path)
    from flowfield.execution_models import WorkerResult

    execution.started("harbor", run.id)
    execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary="Retained report", checks="Retained evidence"),
        commit="b" * 40,
    )
    with execution.workspace.connection(write=True) as db:
        data = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run.id,)).fetchone()[0])
        data.pop("launch_reason", None)
        data.update(
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:01:00Z",
            usage={"total_tokens": 123},
        )
        db.execute(
            "UPDATE runs SET data=?,status='in_review' WHERE id=?", (json.dumps(data), run.id)
        )
    with closing(sqlite3.connect(execution.workspace.database, isolation_level=None)) as db:
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'usage_%'"
        ).fetchall():
            db.execute(f'DROP TRIGGER "{name}"')
        for table in (
            "usage_observations",
            "usage_checkpoints",
            "usage_closed_scopes",
            "usage_executions",
            "usage_metadata",
        ):
            db.execute(f"DROP TABLE {table}")
        if version == 86:
            db.execute("DROP TABLE task_deletions")
        db.execute(f"PRAGMA user_version={version}")
        before = {
            "runs": list(db.execute("SELECT id,data,assignment FROM runs")),
            "results": list(db.execute("SELECT id,data,run_id FROM result_versions")),
        }
        identity = storage.identity(db)
    artifact = execution.workspace.directory / "artifacts" / "retained.bin"
    artifact.write_bytes(b"unchanged result bytes")
    return execution.workspace.directory, run, before, identity


@pytest.mark.parametrize("version", [86, 87, 89])
def test_upgrade_preserves_legacy_without_fabricated_tokens(tmp_path, version):
    directory, run, before, identity = legacy_store(tmp_path, version)
    migrated = Workspace(directory)
    with migrated.connection() as db:
        assert [tuple(r) for r in db.execute("SELECT id,data,assignment FROM runs")] == before[
            "runs"
        ]
        assert storage.identity(db) == identity
        assert [
            tuple(r) for r in db.execute("SELECT id,data,run_id FROM result_versions")
        ] == before["results"]
    assert (directory / "artifacts/retained.bin").read_bytes() == b"unchanged result bytes"
    (backup,) = (directory / "backups").glob(f"schema-{version}-*.sqlite3")
    with closing(storage.connect(backup, readonly=True)) as db:
        assert storage.version(db) == version
        assert storage.identity(db) == identity
    view = UsageReads(migrated).view("harbor", UsageFilter(), task=run.task_id)
    assert view.totals.tokens.total is None
    assert view.totals.legacy == 1
    assert view.totals.recorded_seconds == 60
    assert view.items[0].purpose == "unknown"
    assert view.items[0].coverage == "unavailable"
    assert view.collection_started_at > "2026-01-01"
    backup_bytes = backup.read_bytes()
    Workspace(directory)
    assert backup.read_bytes() == backup_bytes


def test_interrupted_upgrade_rolls_back_and_retries(tmp_path, monkeypatch):
    directory, _, before, identity = legacy_store(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(
            usage_schema, "BACKFILL", usage_schema.BACKFILL + "\nINSERT INTO missing VALUES(1);\n"
        )
        with pytest.raises(sqlite3.OperationalError, match="missing"):
            Workspace(directory)
    with closing(storage.connect(directory / storage.DATABASE)) as db:
        assert storage.version(db) == 89
        assert list(db.execute("SELECT id,data,assignment FROM runs")) == before["runs"]
        assert storage.identity(db) == identity
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='usage_executions'"
        ).fetchone()
    Workspace(directory)
    assert storage.status(directory)["schema_version"] == 90


def test_upgrade_requires_exclusive_execution_ownership(tmp_path):
    directory, _, _, _ = legacy_store(tmp_path)
    with storage.acquire_lock(directory, ".execution.lock"):
        with pytest.raises(Exception, match="Unsupported database version 89"):
            Workspace(directory)
    assert not (directory / "backups").exists()
    Workspace(directory)


def test_unpublished_usage_87_is_not_a_supported_release(tmp_path):
    execution, _, _ = accounting(tmp_path)
    with closing(storage.connect(execution.workspace.database)) as db:
        db.execute("PRAGMA user_version=87")
    before = execution.workspace.database.read_bytes()
    with pytest.raises(ApplicationError, match="unpublished Usage schema"):
        Workspace(execution.workspace.directory)
    assert execution.workspace.database.read_bytes() == before
    assert not (execution.workspace.directory / "backups").exists()


def test_process_exit_before_migration_commit_leaves_old_store(tmp_path):
    import subprocess
    import sys

    directory, _, before, identity = legacy_store(tmp_path)
    code = """
import os, sys
from pathlib import Path
from flowfield import storage
from flowfield.application import Workspace
check = storage.integrity
def interrupted(db):
    check(db)
    if storage.version(db) == 90:
        os._exit(17)
storage.integrity = interrupted
Workspace(Path(sys.argv[1]))
"""
    result = subprocess.run([sys.executable, "-c", code, str(directory)], check=False)
    assert result.returncode == 17
    with closing(storage.connect(directory / storage.DATABASE)) as db:
        assert storage.version(db) == 89
        assert list(db.execute("SELECT id,data,assignment FROM runs")) == before["runs"]
        assert storage.identity(db) == identity
    Workspace(directory)


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_process_exit_observation_checkpoint_commit_is_atomic(tmp_path, boundary):
    import subprocess
    import sys

    from usage_fixtures import event

    from flowfield.usage import Usage

    execution, _, (run,) = accounting(tmp_path)
    observation = event(run)
    code = """
import os, sqlite3, sys
from pathlib import Path
from flowfield.application import Workspace
from flowfield.usage import Usage
from flowfield.usage_models import Consumption
workspace = Workspace(Path(sys.argv[1]))
connect = sqlite3.connect
def instrumented(*args, **kwargs):
    db = connect(*args, **kwargs)
    def crash(statement):
        if statement.startswith('INSERT INTO usage_checkpoints'):
            os._exit(17)
    if sys.argv[4] == 'before':
        db.set_trace_callback(crash)
    return db
sqlite3.connect = instrumented
Usage(workspace).observe('harbor', 'run', sys.argv[2], Consumption.model_validate_json(sys.argv[3]))
os._exit(17)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(execution.workspace.directory),
            run.id,
            observation.model_dump_json(),
            boundary,
        ],
        check=False,
    )
    assert result.returncode == 17
    with execution.workspace.connection() as db:
        assert db.execute("SELECT count(*) FROM usage_observations").fetchone()[0] == (
            boundary == "after"
        )
    Usage(execution.workspace).observe("harbor", "run", run.id, observation)
    view = UsageReads(execution.workspace).view("harbor", UsageFilter())
    assert view.totals.tokens.total == 180
    assert view.items[0].observation_count == 1
