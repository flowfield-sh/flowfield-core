"""Workspace chat ownership and lossless, recoverable migration of its evidence."""

import base64
from uuid import uuid4

import pytest
from coordinator_fixtures import message
from coordinator_generations_fixtures import CODEX, finish_coordinator, start_coordinator
from execution_fixtures import fixture
from fastapi.testclient import TestClient
from project_fixtures import existing_directory
from storage_fixtures import logical_data

from flowfield import migrations
from flowfield.agent_models import AgentSettingsEdit
from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.attachments import Attachments, AttachmentUpload
from flowfield.coordinator_models import CoordinatorTaskSelection
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.migrations import Migration
from flowfield.permission_models import PermissionOption, PermissionRecord
from flowfield.permissions import Permissions
from flowfield.run_activity import ActivityUpdate
from flowfield.workspaces import WorkspaceCreate, Workspaces


def legacy_evidence(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 59)
        )
        store = fixture(tmp_path).workspace
        owner, turn, source = start_coordinator(store)
        files = Attachments(store)
        upload = AttachmentUpload(
            name="notes.txt",
            mime="text/plain",
            data=base64.b64encode(b"Keep this evidence").decode(),
        )
        attachment = files.upload("harbor", upload)
        worker_attachment = files.upload("harbor", upload.model_copy(update={"task_id": "task-0"}))
        owner.write(
            "harbor", turn.id, [ActivityUpdate(key="reply", kind="agent", text="Saved reply 🦉")]
        )
        with store.connection(write=True) as db:
            turn = owner._get(db, "harbor", turn.id)
            turn.text += " " + attachment.href
            files.references(db, "harbor", None, turn.text, bind=True)
            owner._save(db, turn)
            record = PermissionRecord(
                id=uuid4().hex,
                project_id="harbor",
                role="coordinator",
                binding="old-binding",
                turn_id=turn.id,
                conversation_id=turn.conversation_id,
                tool_id="check",
                title="Run check",
                options=[PermissionOption(id="allow", label="Allow once", kind="allow_once")],
                created_at=turn.created_at,
                updated_at=turn.created_at,
                expires_at=turn.created_at,
                status="answered",
                answer="allow",
            )
            db.execute(
                "INSERT INTO agent_permissions(id,project_id,binding,status,data) "
                "VALUES (?,?,?,?,?)",
                (record.id, "harbor", record.binding, record.status, record.model_dump_json()),
            )
        finish_coordinator(owner, turn, cleanup=False)
        with store.connection() as db:
            saved = {
                table: list(map(tuple, db.execute(f"SELECT * FROM {table}")))
                for table in ("coordinator_handoffs", "coordinator_prose")
            }
    return store, turn, source, attachment, worker_attachment, record, saved


def test_upgrade_retains_chat_files_permissions_and_uncertain_native_owner(tmp_path, monkeypatch):
    old, turn, source, attachment, worker_attachment, record, saved = legacy_evidence(
        tmp_path, monkeypatch
    )
    store = Workspace(old.directory)
    owner = CoordinatorStore(store)
    current = owner.get("harbor", turn.id)
    assert current.workspace_id == "harbor" and current.text == turn.text
    assert current.status == "uncertain" and current.number == turn.number
    assert current.settings == turn.settings and current.activity == turn.activity
    with store.connection() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        generation, fresh = owner.sessions.current(db, "harbor")
        assert fresh and generation.id == source.id and generation.session_id == "native"
        for table, rows in saved.items():
            assert list(map(tuple, db.execute(f"SELECT * FROM {table}"))) == rows
        permission = Permissions._get(db, "harbor", record.id, workspace_scope=True)
        assert permission.workspace_id == "harbor" and permission.project_id is None
        assert permission.answer == "allow" and permission.binding == record.binding
    assert (
        Attachments(store).inputs("harbor", None, current.text, workspace_scope=True)[0]["data"]
        == base64.b64encode(b"Keep this evidence").decode()
    )
    with pytest.raises(ApplicationError, match="active turn"):
        owner.reserve("harbor", current.conversation_id, message())
    with TestClient(create_app(data_dir=store.directory), base_url="http://localhost") as client:
        assert client.get(attachment.href).content == b"Keep this evidence"
        assert client.get(worker_attachment.href).content == b"Keep this evidence"
        assert client.get(attachment.href.replace("harbor", "other")).status_code == 404
        page = client.get("/api/workspaces/harbor/permissions").json()
        assert page["items"][0]["status"] == "cancelled"  # Saved answers are never replayed.


def test_ownership_migration_failure_restores_complete_reference_graph(tmp_path, monkeypatch):
    store, *_ = legacy_evidence(tmp_path, monkeypatch)
    before = logical_data(store.directory)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 59)

    def fail(db):
        migration.apply(db)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        raise ValueError("fixture ownership failure")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(59, fail) if m.version == 59 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    Workspace(store.directory)


def test_empty_workspace_chat_and_explicit_project_context(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    other = spaces.create(WorkspaceCreate(name="Other"))
    spaces.configure_coordinator(space.id, AgentSettingsEdit(expected_revision=1, selection=CODEX))
    owner = CoordinatorStore(store)
    conversation = owner.new(space.id)
    turn, _ = owner.reserve(space.id, conversation.id, message("Plan the workspace"))
    assert turn.workspace_id == space.id and turn.task_context is None
    owner.restart()
    assert owner.get(space.id, turn.id).status == "interrupted"
    for identity, workspace in (("core", space), ("site", space), ("outside", other)):
        spaces.adopt(
            workspace.id, ProjectSetup(id=identity, path=existing_directory(tmp_path / identity))
        )
    assert owner.new(space.id).id == conversation.id
    from project_fixtures import task_request

    for project in ("core", "site", "outside"):
        store.create_task(project, task_request(id="same", title=project))
    task = store.task("site", "same")
    request = message("Discuss this task")
    request.task_context = CoordinatorTaskSelection(
        project_id="site", task_id=task.id, task_revision=task.revision
    )
    selected, _ = owner.reserve(space.id, conversation.id, request)
    assert selected.task_context.project_id == "site"
    request.task_context.project_id = "core"
    with pytest.raises(ApplicationError, match="different content"):
        owner.reserve(space.id, conversation.id, request)
    request.task_context.project_id = "outside"
    with pytest.raises(ApplicationError, match="in this workspace"):
        owner.reserve(space.id, conversation.id, request)
    with pytest.raises(ApplicationError, match="not found"):
        owner.get(other.id, selected.id)


def test_upgrade_refuses_to_merge_existing_project_conversations(tmp_path, monkeypatch):
    from project_fixtures import adopt

    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 59)
        )
        store = fixture(tmp_path).workspace
        adopt(store, ProjectSetup(path=str(tmp_path / "other")))
        owner = CoordinatorStore(store)
        owner.new("harbor")
        owner.new("other")
        with store.connection(write=True) as db:
            db.execute(
                "UPDATE workspace_projects SET workspace_id='harbor' WHERE project_id='other'"
            )
    before = logical_data(store.directory)
    with pytest.raises(ApplicationError, match="rolled back"):
        Workspace(store.directory)
    assert logical_data(store.directory) == before
