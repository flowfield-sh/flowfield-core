"""Documents retain exact evidence across edits, concurrent writes, restores and upgrades."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from coordinator_fixtures import setup
from execution_fixtures import BASE, fixture
from fastapi.testclient import TestClient
from storage_fixtures import logical_data

from flowfield import migrations
from flowfield.agent_tools import workspace_scope
from flowfield.api import create_app
from flowfield.application import Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.documents import DocumentCreate, DocumentEdit, DocumentRestore, Documents
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.migrations import Migration
from flowfield.reads import PAGE_BYTES, ContextReads, size
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_document_history_restores_and_concurrent_edits_preserve_sources(tmp_path):
    store = Workspace(tmp_path / "state")
    assert Workspaces(store).all() == []
    space = Workspaces(store).create(WorkspaceCreate(name="Product"))
    docs = Documents(store)
    main = docs.get(space.id, "guidance")
    assert main.body == "" and main.revision == 1
    initial = docs.create(space.id, DocumentCreate(title="Architecture", body="Original"))

    def write(body):
        try:
            return docs.edit(space.id, initial.id, DocumentEdit(expected_revision=1, body=body))
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(write, ["First", "Second"]))
    assert outcomes.count("revision_conflict") == 1
    edited = docs.get(space.id, initial.id)
    assert edited.revision == 2 and edited.body in {"First", "Second"}
    assert docs.get(space.id, initial.id, 1) == initial
    restored = docs.restore(space.id, initial.id, DocumentRestore(expected_revision=2, revision=1))
    assert restored.body == "Original" and restored.revision == 3 and restored.restored_from == 1
    assert docs.get(space.id, initial.id, 2) == edited
    assert docs.history(space.id, initial.id, limit=1).next_cursor == 3
    assert [v.revision for v in docs.history(space.id, initial.id, before=3).items] == [2, 1]
    assert (
        docs.edit(space.id, initial.id, DocumentEdit(expected_revision=3, body="Original"))
        == restored
    )
    with pytest.raises(ApplicationError, match="stale"):
        docs.restore(space.id, initial.id, DocumentRestore(expected_revision=2, revision=1))
    other = Workspaces(store).create(WorkspaceCreate(name="Other"))
    with pytest.raises(ApplicationError, match="not found"):
        docs.get(other.id, initial.id)
    assert Documents(Workspace(store.directory)).get(space.id, initial.id) == restored


def test_document_http_and_scoped_tools_are_bounded_and_attributed(tmp_path):
    store = Workspace(tmp_path / "state")
    space = Workspaces(store).create(WorkspaceCreate(name="Product"))
    docs = Documents(store)
    with TestClient(create_app(data_dir=store.directory), base_url="http://localhost") as client:
        base = f"/api/workspaces/{space.id}/documents"
        created = client.post(base, json={"title": "Design", "body": "🦉" * 9000}).json()
        assert client.get(base).json()["items"][0].get("body") is None
        assert (
            client.put(
                base + "/" + created["id"], json={"expected_revision": 1, "title": "Architecture"}
            ).status_code
            == 200
        )
        assert (
            client.put(
                base + "/" + created["id"], json={"expected_revision": 1, "body": "Lost"}
            ).status_code
            == 409
        )
        assert (
            client.get(base + "/" + created["id"], params={"revision": 1}).json()["title"]
            == "Design"
        )
        assert (
            client.post(
                base + "/" + created["id"] + "/restore",
                json={"expected_revision": 2, "revision": 1},
            ).json()["revision"]
            == 3
        )

        async def check():
            grant = await workspace_scope(Supervisor(store), space.id, author="coordinator:fixture")
            result = await grant.call("get_document", {"document_id": created["id"]})
            assert size(result.structuredContent) < PAGE_BYTES
            assert result.structuredContent["truncated_fields"]["body"]["total_characters"] == 9000
            text = await grant.call(
                "get_document_text", {"document_id": created["id"], "revision": 1}
            )
            assert text.structuredContent["text"] == "🦉" * 4000
            assert text.structuredContent["next_offset"] == 4000
            saved = await grant.call(
                "edit_document",
                {
                    "document_id": "guidance",
                    "changes": {
                        "expected_revision": 1,
                        "body": "Agreed guidance",
                        "author": "human",
                    },
                },
            )
            assert not saved.isError
            assert docs.get(space.id, "guidance").author == "coordinator:fixture"
            assert (
                await grant.call(
                    "get_document", {"workspace_id": "elsewhere", "document_id": "guidance"}
                )
            ).isError
            grant.revoke()

        asyncio.run(check())


def test_guidance_revision_is_frozen_at_send_and_retrievable_after_edit(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    store, docs = service.coordinator.store, Documents(service.workspace)
    main = docs.edit("harbor", "guidance", DocumentEdit(expected_revision=1, body="🦉" * 9000))
    docs.create("harbor", DocumentCreate(title="Unrelated", body="Do not load me"))
    request = CoordinatorSend(id=uuid4().hex, text="Proceed")
    turn, _ = store.reserve("harbor", conversation.id, request)
    assert turn.guidance_revision == main.revision
    docs.edit("harbor", "guidance", DocumentEdit(expected_revision=2, body="New guidance"))
    assert store.reserve("harbor", conversation.id, request) == (turn, False)
    prompt = json.loads(service.coordinator._prompt(turn, "scoped", resumed=True))
    assert prompt["workspace_guidance"]["revision"] == 2
    assert prompt["workspace_guidance"]["truncated_fields"]["body"]["total_characters"] == 9000
    assert "Do not load me" not in json.dumps(prompt)
    assert docs.text("harbor", "guidance", 2)["text"] == "🦉" * 4000
    with service.workspace.connection(write=True) as db:
        turn.status = "completed"
        store._save(db, turn)
    next_turn, _ = store.reserve(
        "harbor", conversation.id, CoordinatorSend(id=uuid4().hex, text="Next")
    )
    assert next_turn.guidance_revision == 3
    assert (
        ContextReads(service.workspace).coordinator_history("harbor", before=next_turn.number)[
            "items"
        ][0]["guidance_revision"]
        == 2
    )
    handoff = json.loads(service.coordinator._prompt(next_turn, "scoped", budget=60000))
    assert handoff["recent_conversation"][0]["guidance_revision"] == 2


def test_document_upgrade_preserves_running_assignment_and_rolls_back(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 64)
        )
        execution = fixture(tmp_path)
        run = execution.claim("harbor", BASE, {BASE: set()})
        assignment = execution.assignment("harbor", run.id)
        store = execution.workspace
        original = store.task("harbor", "task-0")
    before = logical_data(store.directory)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 64)

    def fail(db):
        migration.apply(db)
        raise ValueError("fixture document migration failure")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(64, fail) if m.version == 64 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    upgraded = Workspace(store.directory)
    assert Documents(upgraded).get("harbor", "guidance").body == ""
    assert upgraded.task("harbor", "task-0") == original
    assert Execution(upgraded).assignment("harbor", run.id) == assignment
    with upgraded.connection() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_document_view_binds_exact_revision_and_rejects_other_workspaces(tmp_path, monkeypatch):
    from flowfield.coordinator_models import CoordinatorViewSelection

    service, conversation = setup(tmp_path, monkeypatch)
    docs = Documents(service.workspace)
    original = docs.create("harbor", DocumentCreate(title="Architecture", body="Original"))
    docs.edit("harbor", original.id, DocumentEdit(expected_revision=1, title="New title"))
    request = CoordinatorSend(
        id=uuid4().hex,
        text="Discuss this version",
        view_context=CoordinatorViewSelection(
            surface="document", document_id=original.id, document_revision=1
        ),
    )
    turn, _ = service.coordinator.store.reserve("harbor", conversation.id, request)
    assert turn.view_context.document_title == "Architecture"
    assert turn.view_context.document_revision == 1
    other = Workspaces(service.workspace).create(WorkspaceCreate(name="Other"))
    foreign = docs.create(other.id, DocumentCreate(title="Private"))
    for view in [
        CoordinatorViewSelection(surface="document", document_id=foreign.id, document_revision=1),
        CoordinatorViewSelection(surface="document", document_id=original.id),
        CoordinatorViewSelection(surface="board", document_id=original.id, document_revision=1),
        CoordinatorViewSelection(surface="documents", project_id="harbor"),
    ]:
        with pytest.raises(ApplicationError):
            service.coordinator.store.reserve(
                "harbor",
                conversation.id,
                request.model_copy(update={"id": uuid4().hex, "view_context": view}),
            )


def test_document_delete_restore_preserves_sources_and_guards_in_use(tmp_path):
    from flowfield.application import Edit, ProjectEdit, TaskEdit
    from flowfield.document_models import DocumentReference
    from flowfield.worker_documents import sections, select

    execution = fixture(tmp_path)
    store, docs = execution.workspace, Documents(execution.workspace)
    value = docs.create("harbor", DocumentCreate(title="Contract", body="Frozen contract"))
    with store.connection() as db:
        frozen = select(db, "harbor", [], [DocumentReference(id=value.id)])
    project = store.project("harbor")
    store.edit_project(
        "harbor",
        ProjectEdit(expected_revision=project.revision, worker_documents=[{"id": value.id}]),
    )
    with pytest.raises(ApplicationError, match="Remove this document"):
        docs.delete("harbor", value.id, Edit(expected_revision=1))
    project = store.project("harbor")
    store.edit_project(
        "harbor", ProjectEdit(expected_revision=project.revision, worker_documents=[])
    )
    task = store.task("harbor", "task-0")
    task = store.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, documents=[{"id": value.id}])
    )
    with pytest.raises(ApplicationError, match="Remove this document"):
        docs.delete("harbor", value.id, Edit(expected_revision=1))
    store.edit_task("harbor", task.id, TaskEdit(expected_revision=task.revision, documents=[]))
    deleted = docs.delete("harbor", value.id, Edit(expected_revision=1, author="human"))
    assert deleted.deleted_at and deleted.deleted_by == "human"
    assert deleted.revision == 2
    assert value.id not in [item.id for item in docs.page("harbor").items]
    assert docs.get("harbor", value.id, 1).body == value.body
    assert [r.revision for r in docs.history("harbor", value.id).items] == [2, 1]
    with store.connection() as db:
        assert sections(db, frozen)[f"document:{value.id}"] == value.body
    with pytest.raises(ApplicationError, match="stale"):
        docs.edit("harbor", value.id, DocumentEdit(expected_revision=1, body="Race"))
    with pytest.raises(ApplicationError, match="Restore this document"):
        docs.edit("harbor", value.id, DocumentEdit(expected_revision=2, body="Race"))
    with pytest.raises(ApplicationError, match="not deleted"):
        docs.delete("harbor", "guidance", Edit(expected_revision=1))
    restored = docs.restore("harbor", value.id, DocumentRestore(expected_revision=2, revision=1))
    assert restored.revision == 3 and restored.deleted_at is None
    assert restored.body == value.body
    assert value.id in [item.id for item in docs.page("harbor").items]


def test_document_deletion_upgrade_and_rollback(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 66)
        )
        store = Workspace(tmp_path / "state")
        space = Workspaces(store).create(WorkspaceCreate(name="Existing"))
    before = logical_data(store.directory)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 66)

    def fail(db):
        migration.apply(db)
        raise ValueError("fixture rollback")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(66, fail) if m.version == 66 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    upgraded = Workspace(store.directory)
    assert Documents(upgraded).get(space.id, "guidance").deleted_at is None
    with upgraded.connection() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_document_delete_tools_and_http_preserve_attribution_and_count(tmp_path):
    from flowfield.browser import BrowserReads

    store = Workspace(tmp_path / "state")
    space = Workspaces(store).create(WorkspaceCreate(name="Product"))
    docs = Documents(store)
    doc = docs.create(space.id, DocumentCreate(title="Removable", body="Keep history"))

    async def remove():
        grant = await workspace_scope(Supervisor(store), space.id, author="coordinator:test")
        result = await grant.call(
            "delete_document",
            {"document_id": doc.id, "changes": {"expected_revision": 1, "author": "human"}},
        )
        assert not result.isError
        grant.revoke()

    asyncio.run(remove())
    assert docs.get(space.id, doc.id).deleted_by == "coordinator:test"
    assert BrowserReads(store).workspace_board(space.id).document_count == 1
    with TestClient(create_app(data_dir=store.directory), base_url="http://localhost") as client:
        base = f"/api/workspaces/{space.id}/documents/{doc.id}"
        assert client.get(base, params={"revision": 1}).json()["body"] == "Keep history"
        restored = client.post(base + "/restore", json={"expected_revision": 2, "revision": 1})
        assert restored.status_code == 200
        assert client.request("DELETE", base, json={"expected_revision": 3}).status_code == 200
        assert client.request("DELETE", base, json={"expected_revision": 3}).status_code == 409


def test_document_attribution_uses_task_feed_actor_roles():
    from flowfield.actors import actor
    from flowfield.documents import DocumentHeader

    for identity in ("human", "coordinator:session", "worker:attempt", "flowfield", "migration"):
        record = DocumentHeader(
            workspace_id="work",
            id="guidance",
            revision=1,
            title="Guidance",
            created_at="2026-10-12T00:00:00Z",
            author=identity,
            characters=0,
        )
        assert record.actor == actor(identity)
        assert record.actor.role != "other"


def test_restore_respects_capacity_atomically_without_blocking_active_revisions(tmp_path):
    from flowfield.application import Edit

    store = Workspace(tmp_path / "state")
    space = Workspaces(store).create(WorkspaceCreate(name="Product"))
    docs = Documents(store)
    retired = docs.create(space.id, DocumentCreate(title="Retired", body="Keep this evidence"))
    deleted = docs.delete(space.id, retired.id, Edit(expected_revision=1))
    other = docs.create(space.id, DocumentCreate(title="Other retired", body="Other evidence"))
    docs.delete(space.id, other.id, Edit(expected_revision=1))
    for index in range(99):
        docs.create(space.id, DocumentCreate(title=f"Document {index}"))

    with pytest.raises(ApplicationError) as rejected:
        docs.restore(space.id, retired.id, DocumentRestore(expected_revision=2, revision=1))
    assert rejected.value.code == "document_limit"
    assert docs.get(space.id, retired.id) == deleted
    assert len(docs.history(space.id, retired.id).items) == 2
    # Restoring the content of an active document consumes no new slot.
    assert docs.restore(space.id, "guidance", DocumentRestore(expected_revision=1, revision=1))
    spare = docs.page(space.id).items[1]
    docs.delete(space.id, spare.id, Edit(expected_revision=spare.revision))

    def restore(identity):
        try:
            return docs.restore(
                space.id, identity, DocumentRestore(expected_revision=2, revision=1)
            )
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(restore, [retired.id, other.id]))
    assert outcomes.count("document_limit") == 1
    assert docs.get(space.id, retired.id).body == retired.body
    assert docs.get(space.id, other.id).body == other.body
    assert (
        sum(docs.get(space.id, identity).deleted_at is None for identity in [retired.id, other.id])
        == 1
    )
    with store.connection() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM workspace_documents "
                "WHERE workspace_id=? AND deleted_at IS NULL",
                (space.id,),
            ).fetchone()[0]
            == 100
        )
