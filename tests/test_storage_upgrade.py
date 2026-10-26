"""Upgrade the published 0.6/0.7 schema without changing authored or delivered evidence."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from flowfield import storage
from flowfield.application import TaskCreate, Workspace
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError


def published(tmp_path, version=86):
    fixtures = Path(__file__).parent / "fixtures"
    directory = tmp_path / "state"
    directory.mkdir()
    sql = (
        (fixtures / "published-schema-86.sql")
        .read_text()
        .replace("/fixture/schema-86", str(tmp_path))
    )
    with sqlite3.connect(directory / storage.DATABASE) as db:
        db.executescript(sql)
        if version == 87:
            db.execute(storage.TASK_DELETIONS)
            db.execute("PRAGMA user_version=87")
    info = json.loads((fixtures / "published-schema-86.json").read_text())
    output = directory / "workspaces" / "harbor" / "outputs" / info["output_id"]
    output.parent.mkdir(parents=True)
    output.write_text(info["output_text"])
    return directory, output, info


def rows(database):
    with sqlite3.connect(database) as db:
        names = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'search_docs%' AND name!='task_deletions'"
            )
        ]
        return {name: db.execute(f'SELECT * FROM "{name}"').fetchall() for name in names}


@pytest.mark.parametrize("version", [86, 87])
def test_published_upgrade_preserves_every_row_and_file_with_verified_recovery(tmp_path, version):
    directory, output, info = published(tmp_path, version)
    before = rows(directory / storage.DATABASE)
    store = Workspace(directory)
    assert rows(store.database) == before
    assert storage.status(directory)["schema_version"] == 88
    assert hashlib.sha256(output.read_bytes()).hexdigest() == info["sha256"]
    backups = list((directory / "backups").glob(f"schema-{version}-*.sqlite3"))
    assert len(backups) == 1 and rows(backups[0]) == before
    with sqlite3.connect(backups[0]) as db:
        assert storage.version(db) == version
        storage.integrity(db)
    board = BrowserReads(store).workspace_board("harbor")
    assert {task.status for task in board.tasks} == {"done", "backlog"}
    assert store.task("harbor", "T-1").status == "done"
    Workspace(directory)
    assert list((directory / "backups").glob("*.sqlite3")) == backups
    new = store.create_task("harbor", TaskCreate(title="First edit after upgrade"))
    assert new.key == "T-3"


def test_upgrade_failure_rolls_back_and_retry_preserves_recovery(tmp_path, monkeypatch):
    directory, output, _ = published(tmp_path)
    before = rows(directory / storage.DATABASE)
    migration = storage.TASK_DELETIONS
    monkeypatch.setattr(storage, "TASK_DELETIONS", "CREATE TABLE tasks (id TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        Workspace(directory)
    assert storage.status(directory)["schema_version"] == 86
    assert rows(directory / storage.DATABASE) == before
    assert output.exists()
    monkeypatch.setattr(storage, "TASK_DELETIONS", migration)
    assert Workspace(directory).task("harbor", "T-1").status == "done"


def test_upgrade_requires_exclusive_execution_and_old_open_store_cannot_write(tmp_path):
    directory, _, _ = published(tmp_path)
    before = (directory / storage.DATABASE).read_bytes()
    with storage.acquire_lock(directory, ".execution.lock"):
        with pytest.raises(ApplicationError, match="Preserve"):
            Workspace(directory)
    assert (directory / storage.DATABASE).read_bytes() == before
    assert not (directory / "backups").exists()
    store = Workspace(directory)
    # A process that opened the published schema must not write the upgraded database.
    store.schema_version = 86
    with pytest.raises(ApplicationError, match="opened schema 86"):
        store.create_task("harbor", TaskCreate(title="Stale process"))
