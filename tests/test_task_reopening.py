"""Delivered work can be revised without erasing approval or silently starting execution."""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from result_fixtures import approve, current, fixture

from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_tools import worker_scope, workspace_scope
from flowfield.api import create_app
from flowfield.application import TaskEdit, TaskPreparation, TaskPriority, TaskReopen
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, WorkerResult
from flowfield.supervisor import WorkerBridge


def delivered(tmp_path, completion="code"):
    service, repo, run = fixture(tmp_path, completion=completion, changed=completion == "code")
    service.results.process("harbor")
    if completion == "code":
        approve(service, current(service))
        service.results.process("harbor")
    return service, repo, run


@pytest.mark.parametrize("completion", ["code", "report"])
def test_reopen_preserves_evidence_and_requires_new_preparation_scheduling_and_approval(
    tmp_path, completion
):
    service, repo, old_run = delivered(tmp_path, completion)
    store = service.workspace
    old = current(service)
    saved_run = service.execution.get("harbor", old_run.id)
    head = service.integrations.head("harbor")
    task = store.task("harbor", "work")
    reopened = store.reopen_task(
        "harbor", "work", TaskReopen(expected_revision=task.revision, note="Cover another case")
    )
    assert reopened.status == "backlog" and reopened.publication_status == "draft"
    assert reopened.agreement_revision == task.agreement_revision + 1
    assert service.execution.get("harbor", old_run.id) == saved_run
    retained = service.results.get("harbor", old.id)
    assert retained.status == "delivered" and retained.approved_at == old.approved_at
    assert retained.next_action.label == "Earlier result"
    assert BrowserReads(store).task("harbor", "work").state.label != "Completed"
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    assert service.execution.claim("harbor", head, service._available("harbor", repo, head)) is None
    prepared = store.edit_task(
        "harbor",
        "work",
        TaskEdit(
            expected_revision=reopened.revision,
            body="Deliver the result and cover another case",
            preparation=TaskPreparation(completion=completion),
        ),
    )
    assert prepared.publication_status == "published" and prepared.status == "backlog"
    assert service.execution.claim("harbor", head, service._available("harbor", repo, head)) is None
    assert BrowserReads(store).task("harbor", "work").state.label == "Not scheduled"
    store.prioritize_task(
        "harbor", "work", TaskPriority(expected_revision=prepared.revision, status="up_next")
    )
    run = service.execution.claim("harbor", head, service._available("harbor", repo, head))
    assert run and run.predecessor_id == old_run.id and run.base_commit == head
    assert run.accepted_at is None and run.agreement_revision != old_run.agreement_revision
    assert "another case" in service.execution.assignment("harbor", run.id)["description"]
    service.execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(store.directory, repo, run.id, run.base_commit)
    if completion == "code":
        (env.checkout / "result.txt").write_text("Follow-up implemented")
    commit, _ = env.snapshot(run.base_commit)
    service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        commit=commit,
        result=WorkerResult(summary="Follow-up complete", checks="Both cases checked"),
    )
    service.results.process("harbor")
    next_result = current(service)
    assert next_result.id != old.id and next_result.approved_at is None
    if completion == "code":
        assert next_result.status == "ready" and service.integrations.head("harbor") == head
        approve(service, next_result)
        service.results.process("harbor")
    assert current(service).status == "delivered"
    assert store.task("harbor", "work").status == "done"
    assert service.results.get("harbor", old.id).approved_at == old.approved_at


def test_reopen_rejects_stale_archived_and_unfinished_work(tmp_path):
    service, _, _ = delivered(tmp_path)
    store = service.workspace
    task = store.task("harbor", "work")
    with pytest.raises(ApplicationError, match="stale"):
        store.reopen_task("harbor", task.id, TaskReopen(expected_revision=1, note="Stale"))
    archived = store.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, archived=True)
    )
    with pytest.raises(ApplicationError, match="unarchived completed"):
        store.reopen_task(
            "harbor", task.id, TaskReopen(expected_revision=archived.revision, note="No")
        )
    restored = store.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=archived.revision, archived=False)
    )
    reopened = store.reopen_task(
        "harbor", task.id, TaskReopen(expected_revision=restored.revision, note="Follow-up")
    )
    with pytest.raises(ApplicationError, match="unarchived completed"):
        store.reopen_task(
            "harbor", task.id, TaskReopen(expected_revision=reopened.revision, note="Duplicate")
        )


def test_coordinator_reopen_is_scoped_attributed_and_excluded_from_workers(tmp_path):
    service, _, old_run = delivered(tmp_path)
    task = service.workspace.task("harbor", "work")

    async def exercise():
        grant = await workspace_scope(service, "harbor", author="coordinator:follow-up")
        request = {
            "project_id": "harbor",
            "task_id": "work",
            "reopening": {
                "expected_revision": task.revision,
                "note": "Correct milestone finding",
                "author": "human",
            },
        }
        worker = worker_scope(WorkerBridge(service.execution, old_run, None))
        assert "reopen_task" not in worker.tools
        assert (await worker.call("reopen_task", request)).isError
        result = await grant.call("reopen_task", request)
        assert not result.isError, result
        assert service.workspace.task("harbor", "work").updated_by == "coordinator:follow-up"
        grant.revoke()

    asyncio.run(exercise())


def test_reopen_api_retains_the_delivered_record(tmp_path):
    service, _, _ = delivered(tmp_path)
    task = service.workspace.task("harbor", "work")
    old = current(service)
    with TestClient(
        create_app(data_dir=service.workspace.directory), base_url="http://localhost"
    ) as client:
        response = client.post(
            "/api/projects/harbor/tasks/work/reopen",
            json={
                "expected_revision": task.revision,
                "note": "Prepare the correction",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "backlog"
        assert client.get(f"/api/projects/harbor/results/{old.id}").json()["status"] == "delivered"
