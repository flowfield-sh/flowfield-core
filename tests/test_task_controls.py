"""Task-wide stop and agent deletion reuse lifecycle authority without live models."""

import asyncio

import pytest
from execution_fixtures import fixture as execution_fixture
from result_fixtures import current
from task_reviews_fixtures import configured, verdict

from flowfield.agent_tools import worker_scope, workspace_scope
from flowfield.application import TaskEdit
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.supervisor import Supervisor
from flowfield.worker_tools import WorkerBridge


def test_task_stop_revokes_reviewer_before_cleanup_and_keeps_result(tmp_path):
    service, _, _, _, reviews = configured(tmp_path)
    result = current(service)
    review = reviews.prepare("harbor", result.id)
    review = reviews.claim("harbor", review.id, review.revision)
    review = reviews.started("harbor", review.id, review.revision)
    task = service.workspace.task("harbor", review.task_id)
    assert BrowserReads(service.workspace).task("harbor", task.id).can_stop

    class Client:
        async def stop(self):
            assert service.workspace.task("harbor", task.id).status == "backlog"
            assert reviews.get("harbor", review.id).status == "uncertain"
            with pytest.raises(ApplicationError):
                reviews.conclude(
                    "harbor", review.id, review.revision, verdict(), cleanup_confirmed=True
                )
            return True

    service.clients[review.id] = Client()
    assert (
        asyncio.run(
            service.stop_task("harbor", task.id, RunAction(expected_revision=task.revision))
        )
        == []
    )
    assert reviews.get("harbor", review.id).status == "stopped"
    assert service.results.get("harbor", result.id).report == result.report
    assert not BrowserReads(service.workspace).task("harbor", task.id).can_stop


def test_task_stop_rejects_stale_revision_and_idle_task(tmp_path):
    execution = execution_fixture(tmp_path, count=1)
    service = Supervisor(execution.workspace)
    task = service.workspace.tasks("harbor")[0]
    with pytest.raises(ApplicationError, match="No Worker or Reviewer"):
        asyncio.run(
            service.stop_task("harbor", task.id, RunAction(expected_revision=task.revision))
        )
    run = execution.claim("harbor", "abc", {}, task_id=task.id)
    assert run
    task = service.workspace.task("harbor", task.id)
    edited = service.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, title="New title")
    )
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            service.stop_task("harbor", task.id, RunAction(expected_revision=task.revision))
        )
    assert error.value.code == "revision_conflict"
    assert execution.get("harbor", run.id).status == "preparing"
    assert edited.status != "backlog"


def test_queued_review_cannot_claim_backlogged_task(tmp_path):
    service, _, _, _, reviews = configured(tmp_path)
    review = reviews.prepare("harbor", current(service).id)
    with service.workspace.connection(write=True, workspace_id="harbor") as db:
        task = service.workspace._task(db, "harbor", review.task_id)
        service.execution._task_state(db, task, "backlog", "Paused task")
    with pytest.raises(ApplicationError, match="Backlog"):
        reviews.claim("harbor", review.id, review.revision)
    assert reviews.get("harbor", review.id).status == "queued"


def test_coordinator_can_stop_and_delete_but_worker_cannot(tmp_path):
    execution = execution_fixture(tmp_path, count=1)
    service = Supervisor(execution.workspace)
    task = service.workspace.tasks("harbor")[0]
    run = execution.claim("harbor", "abc", {}, task_id=task.id)
    assert run

    async def exercise():
        coordinator = await workspace_scope(service, "harbor")
        worker = worker_scope(WorkerBridge(execution, run, None))
        assert "delete_task" in coordinator.tools and "stop_task" in coordinator.tools
        assert "delete_task" not in worker.tools and "stop_task" not in worker.tools
        assert coordinator.tools["delete_task"].annotations.destructiveHint
        task = service.workspace.task("harbor", run.task_id)
        blocked = await coordinator.call(
            "delete_task", {"task_id": task.key, "changes": {"expected_revision": task.revision}}
        )
        assert blocked.isError
        stopped = await coordinator.call(
            "stop_task", {"task_id": task.key, "request": {"expected_revision": task.revision}}
        )
        assert not stopped.isError, stopped
        saved = service.workspace.task("harbor", task.id)
        assert saved.updated_by == "agent" and saved.status == "backlog"
        deleted = await coordinator.call(
            "delete_task", {"task_id": task.key, "changes": {"expected_revision": saved.revision}}
        )
        assert not deleted.isError, deleted
        with pytest.raises(ApplicationError):
            service.workspace.task("harbor", task.id)

    asyncio.run(exercise())
