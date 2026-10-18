"""Relevance changes do not silently rewrite agreements or leak workspace sources."""

import asyncio
import json

import pytest
from execution_fixtures import BASE, fixture
from project_fixtures import task_request
from pydantic import ValidationError
from worker_documents_fixtures import defaults, prepare
from workspace_dependencies_fixtures import complete, shared

from flowfield.agent_tools import workspace_scope
from flowfield.application import ProjectEdit, TaskPublish
from flowfield.attention import attention_page
from flowfield.browser import BrowserReads
from flowfield.document_models import DocumentReference
from flowfield.documents import DocumentCreate, DocumentEdit, Documents
from flowfield.errors import ApplicationError
from flowfield.reads import ContextReads
from flowfield.supervisor import Supervisor
from flowfield.worker_context import brief_context
from flowfield.worker_tools import WorkerBridge, worker_tools
from flowfield.workspaces import Workspaces


def test_purpose_is_per_selection_frozen_and_changes_require_preparation(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    doc = Documents(store).create(
        "harbor", DocumentCreate(title="Background", body="Useful detail")
    )
    defaults(store, [{"id": doc.id, "purpose": "reference"}])
    task = prepare(store)
    assert next(d for d in task.publication.documents if d.id == doc.id).purpose == "reference"
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    assignment = execution.assignment("harbor", run.id)
    brief = brief_context(assignment)
    assert f"document:{doc.id}" not in brief["context"]
    assert f"document:{doc.id}" in brief["sections"]

    async def read():
        bridge = WorkerBridge(execution, run, Supervisor(store))
        page = json.loads(await bridge.call("read_context", {"section": f"document:{doc.id}"}))
        assert page["text"] == "Useful detail"

    asyncio.run(read())
    defaults(store, [{"id": doc.id, "purpose": "required"}])
    assert execution.assignment("harbor", run.id) == assignment
    assert store.task("harbor", task.id).publication == task.publication
    assert all(t["name"] != "get_document_impact" for t in worker_tools())
    with pytest.raises(ValidationError, match="always required"):
        DocumentReference(id="guidance", purpose="reference")
    # Another task can override this project's default without changing the document.
    second = store.create_task(
        "harbor",
        task_request(
            title="Other work",
            body="Review this outcome",
            documents=[{"id": doc.id, "purpose": "reference"}],
            preparation={"completion": "report"},
        ),
    )
    assert next(d for d in second.publication.documents if d.id == doc.id).purpose == "reference"


def test_updates_reach_board_inbox_and_coordinator_without_blocking_and_are_scoped(tmp_path):
    store = shared(tmp_path)
    space = Workspaces(store).for_project("core").id
    docs = Documents(store)
    doc = docs.create(space, DocumentCreate(title="Shared contract", body="Original"))
    for project in ("core", "site"):
        store.edit_project(
            project, ProjectEdit(expected_revision=1, worker_documents=[{"id": doc.id}])
        )
        store.create_task(
            space,
            task_request(
                id=project,
                project_id=project,
                title="Apply contract",
                body="Review this outcome",
                preparation={"completion": "report"},
            ),
        )
    before = store.task(space, "core")
    docs.edit(space, doc.id, DocumentEdit(expected_revision=1, body="Changed"))
    current = store.task(space, "core")
    assert current.readiness == before.readiness == "ready"
    assert current.publication == before.publication
    assert current.agreement_revision == before.agreement_revision
    assert len(current.document_updates) == 1
    assert (
        BrowserReads(store).workspace_board(space).tasks[0].document_updates
        == current.document_updates
    )
    assert (
        ContextReads(store).tasks(space)["items"][0]["document_updates"][0]["latest_revision"] == 2
    )
    waiting = attention_page(store, space, "waiting")
    assert {(i.task_key, i.kind) for i in waiting.items} == {
        ("T-1", "document"),
        ("T-2", "document"),
    }
    assert attention_page(store, space, "action").total == 0
    first = docs.impact(space, doc.id, limit=1)
    second = docs.impact(space, doc.id, after=first["next_cursor"], limit=1)
    assert {first["items"][0]["task_key"], second["items"][0]["task_key"]} == {"T-1", "T-2"}
    assert second["next_cursor"] is None
    foreign = Workspaces(store).for_project("elsewhere").id
    with pytest.raises(ApplicationError, match="not found"):
        docs.impact(foreign, doc.id)

    async def read():
        grant = await workspace_scope(Supervisor(store), space)
        assert "workspace_id" not in grant.tools["get_document_impact"].inputSchema["properties"]
        response = await grant.call("get_document_impact", {"document_id": doc.id, "limit": 1})
        assert not response.isError and len(response.structuredContent["items"]) == 1
        rejected = await grant.call(
            "get_document_impact", {"workspace_id": foreign, "document_id": doc.id}
        )
        assert rejected.isError
        grant.revoke()

    asyncio.run(read())
    updated = store.publish_task(
        space,
        current.id,
        TaskPublish(
            expected_revision=current.revision,
            completion="report",
        ),
    )
    assert not updated.document_updates
    complete(store, space, "site")
    assert not docs.impact(space, doc.id)["items"]
    assert attention_page(store, space, "waiting").total == 0
