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
        assert (
            client.get(f"{base}/{plain.id}/content?download=true")
            .headers["content-disposition"]
            .startswith("attachment;")
        )
