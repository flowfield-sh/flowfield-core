"""Durable file identity, scoped access, and failure before publication."""

import shutil

import pytest
from execution_fixtures import BASE, fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerResult
from flowfield.output_api import output_router
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.results import Results
from flowfield.workspaces import Workspaces


def prepared(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)
    runtime = tmp_path / "attempt" / "runtime"
    (runtime / "output").mkdir(parents=True)
    execution.save_local(run.id, {"runtime": str(runtime)})
    owner = Workspaces(execution.workspace).for_project("harbor").id
    return execution, run, runtime / "output", owner


def capture(execution, run, paths):
    with execution.workspace.connection(write=True) as db:
        return Outputs(execution.workspace).capture(
            db, run, [OutputSelection(path=path) for path in paths]
        )


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
