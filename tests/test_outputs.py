"""Durable file identity, scoped access, and failure before publication."""

import shutil

import pytest
from execution_fixtures import BASE
from fastapi import FastAPI
from fastapi.testclient import TestClient
from output_fixtures import prepared

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerResult
from flowfield.output_api import output_router
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.results import Results


def capture(execution, run, paths):
    with Outputs(execution.workspace).capture(
        run, [OutputSelection(path=path) for path in paths]
    ) as files:
        with execution.workspace.connection(write=True) as db:
            Outputs.publish(db, files)
        return files


def test_retained_file_survives_cleanup_restart_and_source_changes(tmp_path):
    execution, run, output, owner = prepared(tmp_path)
    (output / "report.pdf").write_bytes(b"first version")
    first = capture(execution, run, ["report.pdf"])[0]
    (output / "report.pdf").write_bytes(b"second version")
    second = capture(execution, run, ["report.pdf"])[0]
    shutil.rmtree(output.parent.parent)
    store = Workspace(execution.workspace.directory)
    outputs = Outputs(store)
    assert first.id != second.id
    assert outputs.content(outputs.get(owner, first.id)).read_bytes() == b"first version"
    assert outputs.content(outputs.get(owner, second.id)).read_bytes() == b"second version"
    page = outputs.page(owner, limit=1)
    assert page.items == [second]
    assert outputs.page(owner, before=page.next_before).items == [first]
    assert outputs.page(owner, task_key=run.task_key).items == [second, first]
    assert outputs.page(owner, task_key="unrelated").items == []
    with pytest.raises(ApplicationError, match="File not found"):
        outputs.get("another-workspace", first.id)


def test_history_file_retrieval_enforces_frozen_owner_and_exact_bytes(tmp_path):
    import json
    from pathlib import Path

    from flowfield.task_evidence import read_file

    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("original proof")
    file = capture(execution, run, ["proof.txt"])[0]
    root = tmp_path / "successor/evidence"
    for workspace, task, runs in ((owner, run.task_id, set()), (owner, "other-task", {run.id})):
        with pytest.raises(ApplicationError):
            read_file(execution.workspace, workspace, task, file.id, root, run_ids=runs)
    saved = json.loads(
        read_file(execution.workspace, owner, run.task_id, file.id, root, run_ids={run.id})
    )
    target = Path(saved["path"])
    assert target.read_text() == "original proof"
    assert (
        json.loads(
            read_file(execution.workspace, owner, run.task_id, file.id, root, run_ids={run.id})
        )
        == saved
    )
    target.chmod(0o600)
    target.write_text("tampered proof")
    with pytest.raises(ApplicationError, match="changed"):
        read_file(execution.workspace, owner, run.task_id, file.id, root, run_ids={run.id})


def test_schema_90_upgrade_keeps_output_bytes_ids_and_owner(tmp_path):
    import sqlite3

    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("retained evidence")
    file = capture(execution, run, ["proof.txt"])[0]
    with sqlite3.connect(execution.workspace.database) as db:
        db.executescript("""
            CREATE TABLE old_outputs(number INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                run_id TEXT NOT NULL REFERENCES runs(id),data TEXT NOT NULL);
            INSERT INTO old_outputs SELECT number,id,workspace_id,run_id,data FROM output_files;
            DROP TABLE output_files;
            ALTER TABLE old_outputs RENAME TO output_files;
            CREATE INDEX output_run ON output_files(run_id,number);
            CREATE INDEX output_workspace ON output_files(workspace_id,number);
            ALTER TABLE usage_observations DROP COLUMN cost;
            PRAGMA user_version=90;
        """)
        before = db.execute(
            "SELECT number,id,workspace_id,run_id,data FROM output_files"
        ).fetchall()
    store = Workspace(execution.workspace.directory)
    with store.connection() as db:
        assert [
            tuple(row)
            for row in db.execute("SELECT number,id,workspace_id,run_id,data FROM output_files")
        ] == before
        assert db.execute("SELECT review_attempt_id FROM output_files").fetchone()[0] is None
    restored = Outputs(store).get(owner, file.id)
    assert restored.run_id == run.id
    assert Outputs(store).content(restored).read_text() == "retained evidence"
    assert len(list((store.directory / "backups").glob("schema-90-*.sqlite3"))) == 1


@pytest.mark.parametrize("path", ["../secret", "/tmp/secret", "a/../../secret", "a//b", "a\\b"])
def test_output_path_rejects_traversal(path):
    with pytest.raises(ValueError):
        OutputSelection(path=path)


def test_failed_batch_has_no_published_files_and_rejects_links(tmp_path):
    execution, run, output, owner = prepared(tmp_path)
    (output / "good.txt").write_text("good")
    secret = tmp_path / "secret"
    secret.write_text("secret")
    (output / "link").symlink_to(secret)
    for bad in ("link", "missing"):
        with pytest.raises(ApplicationError, match="Could not retain"):
            capture(execution, run, ["good.txt", bad])
        assert Outputs(execution.workspace).page(owner).items == []
    (output / "directory").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ApplicationError):
        capture(execution, run, ["directory/secret"])


def test_capture_bounds_and_missing_file_preserve_metadata(tmp_path, monkeypatch):
    execution, run, output, owner = prepared(tmp_path)
    (output / "large.txt").write_text("12345")
    monkeypatch.setattr("flowfield.outputs.MAX_FILE_BYTES", 4)
    with pytest.raises(ApplicationError, match="128 MiB"):
        capture(execution, run, ["large.txt"])
    assert Outputs(execution.workspace).page(owner).items == []
    monkeypatch.setattr("flowfield.outputs.MAX_FILE_BYTES", 100)
    value = capture(execution, run, ["large.txt"])[0]
    outputs = Outputs(execution.workspace)
    outputs.content(value).unlink()
    with pytest.raises(ApplicationError, match="missing or changed"):
        outputs.content(value)
    assert outputs.get(owner, value.id) == value


def test_file_cannot_be_claimed_in_result_without_capture(tmp_path):
    execution, run, output, owner = prepared(tmp_path)
    report = WorkerResult(summary="Report", checks="Checked", files=[OutputSelection(path="a.txt")])
    with pytest.raises(ApplicationError, match="Retain every"):
        execution.finish("harbor", run.id, "in_review", result=report, commit=BASE)
    assert Results(execution.workspace).page("harbor", run.task_id).items == []
    (output / "a.txt").write_text("original")
    value = capture(execution, run, ["a.txt"])[0]
    execution.finish("harbor", run.id, "in_review", result=report, commit=BASE)
    version = Results(execution.workspace).page("harbor", run.task_id).items[0]
    assert [file.id for file in version.files] == [value.id]
    assert version.files[0].result_id == version.id


def test_download_active_content_is_attachment_and_passive_content_can_open(tmp_path):
    execution, run, output, owner = prepared(tmp_path)
    (output / "page.html").write_text("<script>alert(1)</script>")
    (output / "notes.txt").write_text("Findings")
    html, plain = capture(execution, run, ["page.html", "notes.txt"])
    app = FastAPI()
    app.include_router(output_router(lambda: execution.workspace))
    with TestClient(app) as client:
        base = f"/api/workspaces/{owner}/files"
        response = client.get(f"{base}/{html.id}/content")
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["content-security-policy"].startswith("sandbox;")
        response = client.get(f"{base}/{plain.id}/content")
        assert response.headers["content-disposition"].startswith("inline;")
        assert response.text == "Findings"
        preview = client.get(f"{base}/{html.id}/text")
        assert preview.json() == {
            "text": "<script>alert(1)</script>",
            "supported": True,
            "truncated": False,
        }
        assert preview.headers["content-type"].startswith("application/json")
        assert (
            client.get(f"{base}/{plain.id}/content?download=true")
            .headers["content-disposition"]
            .startswith("attachment;")
        )


def test_text_preview_is_bounded_and_rejects_binary(tmp_path):
    execution, run, output, owner = prepared(tmp_path)
    (output / "large.txt").write_text("x" * 65535 + "🦉" * 10)
    (output / "binary.txt").write_bytes(b"text\x00binary")
    (output / "invalid.txt").write_bytes(b"text\xff")
    large, binary, invalid = capture(execution, run, ["large.txt", "binary.txt", "invalid.txt"])
    app = FastAPI()
    app.include_router(output_router(lambda: execution.workspace))
    with TestClient(app) as client:
        base = f"/api/workspaces/{owner}/files"
        assert client.get(f"{base}/{large.id}/text").json() == {
            "text": "x" * 65535,
            "supported": True,
            "truncated": True,
        }
        for value in (binary, invalid):
            assert client.get(f"{base}/{value.id}/text").json() == {
                "text": "",
                "supported": False,
                "truncated": False,
            }


def test_submission_rollback_removes_only_new_files(tmp_path, monkeypatch):
    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("committed")
    saved = capture(execution, run, ["proof.txt"])[0]
    (output / "proof.txt").write_text("failed successor")

    def fail(*_):
        raise RuntimeError("later transaction failure")

    monkeypatch.setattr(execution, "_save", fail)
    with pytest.raises(RuntimeError, match="later transaction"):
        execution.report_result(
            owner,
            run.id,
            WorkerResult(
                summary="Result", checks="Checked", files=[OutputSelection(path="proof.txt")]
            ),
        )
    retained = execution.workspace.directory / "workspaces" / owner / "outputs"
    assert [path.name for path in retained.iterdir()] == [saved.id]
    assert Outputs(execution.workspace).content(saved).read_text() == "committed"
    assert len(Outputs(execution.workspace).page(owner).items) == 1


@pytest.mark.parametrize("phase", ["partial", "retained", "committed"])
def test_startup_reclaims_crashed_capture_but_keeps_committed_evidence(tmp_path, phase):
    import subprocess
    import sys

    from flowfield.supervisor import Supervisor

    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("survives if committed")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from pathlib import Path
from flowfield.application import Workspace
from flowfield.execution import Execution
from flowfield.outputs import Outputs
from flowfield.output_models import OutputSelection
store = Workspace(Path(sys.argv[1]))
run = Execution(store).get(sys.argv[2], sys.argv[3])
if sys.argv[4] == 'partial':
    import flowfield.outputs
    flowfield.outputs.os.link = lambda *_: os._exit(23)
with Outputs(store).capture(run, [OutputSelection(path='proof.txt')]) as files:
    if sys.argv[4] == 'retained':
        os._exit(23)
    with store.connection(write=True) as db:
        Outputs.publish(db, files)
os._exit(23)
""",
            str(execution.workspace.directory),
            owner,
            run.id,
            phase,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert child.returncode == 23, child.stderr
    retained = execution.workspace.directory / "workspaces" / owner / "outputs"
    assert len(list(retained.iterdir())) == 1
    store = Workspace(execution.workspace.directory)
    Supervisor(store)._recover()
    files = Outputs(store).page(owner).items
    assert len(files) == (1 if phase == "committed" else 0)
    assert len(list(retained.iterdir())) == len(files)
    if files:
        assert Outputs(store).content(files[0]).read_text() == "survives if committed"


def test_recovery_preserves_inflight_capture_and_unrecognized_paths(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("in flight")
    store = execution.workspace
    began = Event()

    def recover():
        began.set()
        Outputs(store).recover()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Outputs(store).capture(run, [OutputSelection(path="proof.txt")]) as files:
            saved = files[0]
            future = pool.submit(recover)
            assert began.wait(5)
            future.result(timeout=5)  # Busy captures are skipped, never reaped.
            with store.connection(write=True) as db:
                Outputs.publish(db, files)
        future.result(timeout=10)
    assert Outputs(store).content(saved).read_text() == "in flight"
    retained = store.directory / "workspaces" / owner / "outputs"
    unrelated = retained / "notes.txt"
    unrelated.write_text("keep")
    target = tmp_path / "outside"
    target.write_text("keep outside")
    link = retained / ("b" * 32)
    link.symlink_to(target)
    Outputs(store).recover()
    assert unrelated.read_text() == "keep" and target.read_text() == "keep outside"
    assert link.is_symlink()


def test_commit_failure_reclaims_new_capture(tmp_path, monkeypatch):
    import sqlite3

    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("not committed")

    original = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def commit(self):
            if not self.execute("PRAGMA query_only").fetchone()[0]:
                raise sqlite3.OperationalError("simulated commit failure")
            super().commit()

    with monkeypatch.context() as patch:
        patch.setattr(
            sqlite3, "connect", lambda *a, **kw: original(*a, **kw, factory=FailingConnection)
        )
        with pytest.raises(sqlite3.OperationalError, match="commit failure"):
            capture(execution, run, ["proof.txt"])
    assert Outputs(execution.workspace).page(owner).items == []
    retained = execution.workspace.directory / "workspaces" / owner / "outputs"
    assert list(retained.iterdir()) == []


@pytest.mark.parametrize("parent", ["workspaces", "workspace", "outputs"])
def test_output_recovery_never_follows_directory_links(tmp_path, parent):
    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("committed")
    saved = capture(execution, run, ["proof.txt"])[0]
    store = execution.workspace
    path = store.directory / "workspaces"
    if parent != "workspaces":
        path /= owner
    if parent == "outputs":
        path /= "outputs"
    original = tmp_path / "original-directory"
    path.rename(original)
    path.symlink_to(original, target_is_directory=True)
    orphan = original / ("c" * 32)
    orphan.write_text("unrelated bytes behind link")
    Outputs(store).recover()
    assert orphan.read_text() == "unrelated bytes behind link"
    assert Outputs(store).content(saved).read_text() == "committed"


def test_capture_collision_cannot_remove_existing_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace

    execution, run, output, owner = prepared(tmp_path)
    (output / "proof.txt").write_text("original")
    saved = capture(execution, run, ["proof.txt"])[0]
    monkeypatch.setattr("flowfield.outputs.uuid4", lambda: SimpleNamespace(hex=saved.id))
    (output / "proof.txt").write_text("replacement")
    with pytest.raises(ApplicationError, match="Could not retain"):
        capture(execution, run, ["proof.txt"])
    assert Outputs(execution.workspace).content(saved).read_text() == "original"
