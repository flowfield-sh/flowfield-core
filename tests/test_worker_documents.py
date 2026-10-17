"""Selected guidance stays scoped, measurable and immutable through worker attempts."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from execution_fixtures import BASE, RESULT, fixture, result, validate_report
from input_continuation_fixtures import answer, question
from project_fixtures import task_request
from worker_documents_fixtures import defaults, prepare

from flowfield.agent_tools import workspace_scope
from flowfield.application import TaskEdit, TaskPublish, TaskReconcile, Workspace
from flowfield.documents import DocumentCreate, DocumentEdit, Documents
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.questions import Questions
from flowfield.results import Results
from flowfield.supervisor import Supervisor
from flowfield.worker_context import brief_context
from flowfield.worker_tools import WorkerBridge, worker_tools
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_preparation_freezes_union_and_workers_page_only_selected_revisions(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    docs = Documents(store)
    shared = docs.create("harbor", DocumentCreate(title="Conventions", body="🦉" * 9000))
    specific = docs.create("harbor", DocumentCreate(title="Feature", body="Keep this constraint"))
    unrelated = docs.create("harbor", DocumentCreate(title="Other project", body="Unrelated"))
    defaults(store, [{"id": shared.id}])
    task = store.task("harbor", "task-0")
    store.edit_task(
        "harbor",
        task.id,
        TaskEdit(
            expected_revision=task.revision,
            documents=[{"id": specific.id}, {"id": shared.id, "revision": 1}],
        ),
    )
    task = prepare(store)
    assert len(task.publication.documents) == 3
    docs.edit("harbor", shared.id, DocumentEdit(expected_revision=1, body="New instructions"))
    current = store.task("harbor", task.id)
    assert current.model_dump(exclude={"document_updates"}) == task.model_dump(
        exclude={"document_updates"}
    )
    assert [(u.id, u.prepared_revision, u.latest_revision) for u in current.document_updates] == [
        (shared.id, 1, 2)
    ]
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run.documents == task.publication.documents
    assignment = execution.assignment("harbor", run.id)
    manifest = json.loads(assignment["documentation"])
    assert manifest["characters"] == 9000 + len(specific.body)
    assert manifest["bytes"] == 36000 + len(specific.body)
    assert f"document:{unrelated.id}" not in assignment
    assert assignment["document:guidance"] == ""
    assert "🦉" not in json.dumps(brief_context(assignment), ensure_ascii=False)
    assert len(json.dumps(brief_context(assignment)["context"])) <= 10000
    assert brief_context(assignment)["context"][f"document:{specific.id}"] == specific.body
    execution.started("harbor", run.id)

    async def reads():
        bridge = WorkerBridge(execution, run, SimpleNamespace())
        first = json.loads(await bridge.call("read_context", {"section": f"document:{shared.id}"}))
        assert first["text"] == "🦉" * 6000 and first["next_offset"] == 6000
        rest = json.loads(
            await bridge.call(
                "read_context",
                {
                    "section": f"document:{shared.id}",
                    "offset": 6000,
                },
            )
        )
        assert rest["text"] == "🦉" * 3000 and rest["next_offset"] is None
        with pytest.raises(ApplicationError, match="Available sections"):
            await bridge.call("read_context", {"section": f"document:{unrelated.id}"})

    asyncio.run(reads())
    assert not any(tool["name"] == "get_document" for tool in worker_tools())
    assert Execution(Workspace(store.directory)).assignment("harbor", run.id) == assignment


def test_new_revisions_require_preparation_and_change_the_agreement_atomically(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    doc = Documents(store).create("harbor", DocumentCreate(title="Design", body="Original"))
    defaults(store, [{"id": doc.id}])
    first = prepare(store)
    assert prepare(store) == first
    Documents(store).edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="Changed"))
    current = store.task("harbor", first.id)
    assert current.model_dump(exclude={"document_updates"}) == first.model_dump(
        exclude={"document_updates"}
    )
    with pytest.raises(ApplicationError, match="current stages"):
        store.publish_task(
            "harbor", first.id, TaskPublish(expected_revision=first.revision, completion="report")
        )
    assert store.task("harbor", first.id) == current  # Failed preparation rolls back scope too.
    refreshed = prepare(store)
    assert refreshed.agreement_revision == first.agreement_revision + 1
    assert refreshed.publication.documents[0].revision == 2
    assert refreshed.readiness == "ready"
    defaults(store, [])
    assert store.task("harbor", first.id) == refreshed
    removed = prepare(store)
    assert removed.agreement_revision == refreshed.agreement_revision + 1
    assert [item.id for item in removed.publication.documents] == ["guidance"]


def test_document_edits_and_default_changes_preserve_answer_continuations(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    doc = Documents(store).create("harbor", DocumentCreate(title="Design", body="Original"))
    defaults(store, [{"id": doc.id}])
    prepare(store)
    run, q = question(execution)
    assignment = execution.assignment("harbor", run.id)
    Documents(store).edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="Changed"))
    defaults(store, [])
    answer(Questions(store), q)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    successor = execution.claim("harbor", BASE, {RESULT: set()})
    assert successor.documents == run.documents
    assert (
        execution.assignment("harbor", successor.id)["documentation"] == assignment["documentation"]
    )
    assert execution.context_section("harbor", successor.id, f"document:{doc.id}") == "Original"


def test_reconciling_new_document_scope_does_not_rebind_old_attempt(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    doc = Documents(store).create("harbor", DocumentCreate(title="Design", body="Original"))
    defaults(store, [{"id": doc.id}])
    prepare(store)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    execution.finish("harbor", run.id, "stopped")
    Documents(store).edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="Changed"))
    current = store.task("harbor", "task-0")
    updated = store.reconcile_task(
        "harbor",
        current.id,
        TaskReconcile(
            expected_revision=current.revision,
            completion="report",
            note="Adopt the revised design.",
        ),
    )
    assert updated.agreement_revision == run.agreement_revision + 1
    assert updated.publication.documents[0].revision == 2
    assert execution.get("harbor", run.id).documents[0].revision == 1
    assert updated.readiness == "needs_reconciliation"  # Stages still need review.


def test_adopting_document_changes_invalidates_the_old_result(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    doc = Documents(store).create("harbor", DocumentCreate(title="Design", body="Original"))
    defaults(store, [{"id": doc.id}])
    prepare(store)
    run = result(execution, process=False)
    Documents(store).edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="New scope"))
    current = store.task("harbor", run.task_id)
    store.reconcile_task(
        "harbor",
        current.id,
        TaskReconcile(
            expected_revision=current.revision,
            completion="report",
            note="Adopt the new scope.",
        ),
    )
    validate_report(execution)
    version = Results(store).page("harbor", run.task_id).items[0]
    assert version.problem_code == "assignment_changed"
    assert execution.context_section("harbor", run.id, f"document:{doc.id}") == "Original"


def test_foreign_missing_and_oversized_sources_do_not_partially_prepare(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    other = Workspaces(store).create(WorkspaceCreate(name="Other"))
    foreign = Documents(store).create(other.id, DocumentCreate(title="Private", body="Other"))
    before = store.project("harbor")
    with pytest.raises(ApplicationError, match="not found"):
        defaults(store, [{"id": foreign.id}])
    assert store.project("harbor") == before
    task = store.task("harbor", "task-0")
    with pytest.raises(ApplicationError, match="not found"):
        store.edit_task(
            "harbor",
            task.id,
            TaskEdit(
                expected_revision=task.revision, documents=[{"id": "guidance", "revision": 999}]
            ),
        )
    assert store.task("harbor", task.id) == task
    docs = [
        Documents(store).create("harbor", DocumentCreate(title=f"Large {n}", body="🦉" * 150000))
        for n in range(2)
    ]
    defaults(store, [{"id": doc.id} for doc in docs])
    with pytest.raises(ApplicationError, match="exceed 1 MB"):
        prepare(store)
    assert store.task("harbor", task.id) == task


def test_scoped_coordinator_can_select_and_prepare_without_document_body_echo(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    doc = Documents(store).create("harbor", DocumentCreate(title="Design", body="Do this"))

    async def select():
        grant = await workspace_scope(Supervisor(store), "harbor", author="coordinator:fixture")
        changed = await grant.call(
            "edit_project",
            {
                "project_id": "harbor",
                "changes": {
                    "expected_revision": store.project("harbor").revision,
                    "worker_documents": [{"id": doc.id}],
                },
            },
        )
        assert not changed.isError
        created = await grant.call(
            "create_task",
            {
                "project_id": "harbor",
                "task": task_request(
                    title="Documented work",
                    body="Review selected design",
                    preparation={"completion": "report"},
                ).model_dump(),
            },
        )
        assert not created.isError
        assert "Do this" not in str(created.structuredContent)
        task = store.task("harbor", created.structuredContent["id"])
        assert task.publication.documents[0].id == doc.id
        grant.revoke()

    asyncio.run(select())


def test_selected_document_policy_reaches_native_turn_unchanged(tmp_path):
    from native_adapters_fixtures import start

    execution = fixture(tmp_path)
    store = execution.workspace
    doc = Documents(store).create(
        "harbor", DocumentCreate(title="Large contract", body="A rule.\n" * 2000)
    )
    defaults(store, [{"id": doc.id}])
    prepare(store)
    run = execution.claim("harbor", BASE, {BASE: set()})
    envelope = brief_context(execution.assignment("harbor", run.id))
    assert f"document:{doc.id}" not in envelope["context"]
    assert (
        "every omitted required document:<id> section in full before acting"
        in envelope["context_policy"]
    )
    payload = json.dumps(envelope, ensure_ascii=False)

    async def verify_wire():
        agent = await start(tmp_path, [])
        frames = []
        send = agent.rpc.send

        async def capture(message):
            await send(message)
            if message.get("method") == "turn/start":
                frames.append(message)

        agent.rpc.send = capture
        try:
            assert await agent.prompt(payload, None) == {"status": "completed"}
            assert frames[0]["params"]["input"] == [{"type": "text", "text": payload}]
            manifest = json.loads(
                json.loads(frames[0]["params"]["input"][0]["text"])["context"]["documentation"]
            )
            assert manifest["items"][0]["revision"] == 1
            assert manifest["items"][0]["characters"] == len(doc.body)
        finally:
            assert await agent.stop()

    asyncio.run(verify_wire())


def test_workspace_guidance_is_required_and_uses_frozen_revisions(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    docs = Documents(store)
    assert [ref.id for ref in store.project("harbor").worker_documents] == ["guidance"]
    assert [ref.id for ref in defaults(store, []).worker_documents] == ["guidance"]
    docs.edit("harbor", "guidance", DocumentEdit(expected_revision=1, body="Shared rule"))
    task = prepare(store)
    assert [(item.id, item.revision) for item in task.publication.documents] == [("guidance", 2)]
    docs.edit("harbor", "guidance", DocumentEdit(expected_revision=2, body="Later rule"))
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert execution.assignment("harbor", run.id)["document:guidance"] == "Shared rule"
    assert run.documents == task.publication.documents
