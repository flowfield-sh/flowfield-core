"""Durable, conversation-scoped human files delivered as native prompt content."""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from test_coordinator import message, settled, setup
from test_execution import fixture
from test_managed_local import configured
from test_replies import message as reply_message

from flowfield import migrations
from flowfield.adapters.git_workspace import baseline
from flowfield.api import create_app
from flowfield.application import Workspace
from flowfield.attachments import MAX_FILE, Attachments, AttachmentUpload
from flowfield.errors import ApplicationError
from flowfield.replies import Replies

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
)


def upload(
    files, *, task=None, name="notes.txt", content=b"Preserve the public API.", mime="text/plain"
):
    return files.upload(
        "harbor",
        AttachmentUpload(
            name=name, data=base64.b64encode(content).decode(), mime=mime, task_id=task
        ),
    )


def attached(files, task=None):
    text = upload(files, task=task)
    image = upload(files, task=task, name="screen.png", content=PNG, mime="image/png")
    return f"Use these references.\n\n[notes.txt]({text.href})\n\n[screen.png]({image.href})"


def test_coordinator_delivers_real_text_images_context_and_durable_download(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch, flags=("expect-attachments",))
    files = Attachments(service.workspace)
    request = message(attached(files))

    async def exercise():
        turn = service.coordinator.send("harbor", conversation.id, request)
        completed = await settled(service, turn)
        assert completed.status == "completed", completed.notice
        assert completed.activity.context.used == 100
        assert completed.activity.context.size == 1000
        assert completed.activity.usage.total_tokens is None
        assert service.coordinator.send("harbor", conversation.id, request).id == turn.id
        await service.close()

    asyncio.run(exercise())
    restored = Workspace(service.workspace.directory)
    assert Attachments(restored).inputs("harbor", None, request.text)[0]["name"] == "notes.txt"
    with TestClient(create_app(data_dir=restored.directory), base_url="http://localhost") as client:
        href = request.text.split("](")[-1].removesuffix(")")
        result = client.get(href)
        assert result.content == PNG and result.headers["content-type"] == "image/png"
        assert result.headers["content-disposition"].startswith("attachment;")
        assert result.headers["x-content-type-options"] == "nosniff"
        assert client.get(href.replace("harbor", "elsewhere")).status_code == 404


def test_worker_answer_delivers_attachments_through_existing_reply_binding(tmp_path, monkeypatch):
    service, repo, _ = configured(
        tmp_path, monkeypatch, scenario="normal", flags=("expect-attachments",)
    )
    from test_input_continuation import question

    base = baseline(repo)
    run, _ = question(service.execution)
    service.execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=base)
    body = attached(Attachments(service.workspace), "task-0")
    request = reply_message(service.workspace, action="answer", body=body)
    receipt = Replies(service.workspace).submit("harbor", "task-0", request)
    assert Replies(service.workspace).submit("harbor", "task-0", request) == receipt

    async def exercise():
        base = baseline(repo)
        run = service.execution.claim("harbor", base, {base: set()})
        assert run.purpose == "work"
        await service._execute(run, repo)
        saved = service.execution.get("harbor", run.id)
        assert saved.status == "in_review", saved.problem
        await service.close()

    asyncio.run(exercise())


def test_attachment_scope_atomic_binding_and_draft_expiry(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    workspace = service.workspace
    files = Attachments(workspace)
    draft = upload(files)
    task = upload(files, task="task-0")
    with pytest.raises(ApplicationError, match="not been sent"):
        files.inputs("harbor", None, draft.href)
    with pytest.raises(ApplicationError, match="unavailable"):
        service.coordinator.store.reserve("harbor", conversation.id, message(task.href))
    with pytest.raises(ApplicationError, match="unavailable"):
        service.coordinator.store.reserve(
            "harbor",
            conversation.id,
            message(draft.href + " " + draft.href.replace("harbor", "elsewhere")),
        )
    with workspace.connection() as db:
        assert not db.execute("SELECT bound FROM attachments WHERE id=?", (draft.id,)).fetchone()[0]
    service.coordinator.store.reserve("harbor", conversation.id, message(draft.href))
    with workspace.connection(write=True) as db:
        db.execute("UPDATE attachments SET created_at='2000-01-01T00:00:00Z'")
    upload(files)
    with workspace.connection() as db:
        assert db.execute("SELECT id FROM attachments WHERE id=?", (draft.id,)).fetchone()
        assert not db.execute("SELECT id FROM attachments WHERE id=?", (task.id,)).fetchone()


@pytest.mark.parametrize(
    "content,mime",
    [
        (b"", "text/plain"),
        (b"x" * 65537, "text/plain"),
        (b"\x00", "application/octet-stream"),
        (b"<svg/>", "image/svg+xml"),
        (b"not png", "image/png"),
        (b"\xff", "text/plain"),
    ],
)
def test_invalid_uploads_are_rejected(tmp_path, content, mime):
    with pytest.raises(ApplicationError):
        upload(Attachments(fixture(tmp_path).workspace), content=content, mime=mime)


def test_http_upload_size_limit_and_restart(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    before = workspace.task("harbor", "task-0")
    restored = Workspace(workspace.directory)
    assert restored.schema_version == migrations.current_version()
    assert restored.task("harbor", "task-0") == before
    with TestClient(create_app(data_dir=restored.directory), base_url="http://localhost") as client:
        path = "/api/projects/harbor/attachments"
        assert client.post(path, content=b"x" * (MAX_FILE * 4 // 3 + 4097)).status_code == 413
        assert client.post(path, json={"data": "bad"}).status_code == 400
        result = client.post(
            path, json={"name": "hello.txt", "mime": "text/plain", "data": "aGVsbG8="}
        )
        assert result.status_code == 201
        assert client.get(result.json()["href"]).content == b"hello"


def test_answer_files_follow_the_frozen_continuation(tmp_path, monkeypatch):
    flags = []
    service, repo, _ = configured(tmp_path, monkeypatch, scenario="question", flags=flags)
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    asyncio.run(service._execute(run, repo))
    paused = service.execution.get("harbor", run.id)
    assert paused.status == "waiting_for_input"
    body = attached(Attachments(service.workspace), "task-0")
    Replies(service.workspace).submit(
        "harbor", "task-0", reply_message(service.workspace, action="answer", body=body)
    )
    successor = service.execution.claim("harbor", base, {paused.input_checkpoint: set()})
    assert successor.input_base_commit == paused.input_checkpoint
    flags.append("expect-attachments")
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", "normal")
    asyncio.run(service._execute(successor, repo))
    saved = service.execution.get("harbor", successor.id)
    assert saved.status == "in_review", saved.problem


def test_unsupported_images_fail_explicitly_without_silently_dropping_them(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch, flags=("no-images",))
    body = attached(Attachments(service.workspace))

    async def exercise():
        turn = service.coordinator.send("harbor", conversation.id, message(body))
        saved = await settled(service, turn)
        assert saved.status == "failed"
        assert "cannot receive images" in saved.notice
        assert [(item.kind, item.text) for item in saved.activity.items] == [
            ("status", "Fresh agent session did not receive this message.")
        ]
        assert (
            service.coordinator.store.session(
                "harbor", "codex", service.workspace.project("harbor").path
            )
            is None
        )
        retry = service.coordinator.send("harbor", conversation.id, message("Use text instead"))
        assert (await settled(service, retry)).session == "new"
        await service.close()

    asyncio.run(exercise())
