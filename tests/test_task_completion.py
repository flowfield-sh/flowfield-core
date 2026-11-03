"""Done is terminal across domain writes and public interfaces."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from result_fixtures import approve, current, fixture
from typer.testing import CliRunner

from flowfield.agent_tools import workspace_scope
from flowfield.api import create_app
from flowfield.application import TaskEdit, TaskPriority, TaskReconcile
from flowfield.cli import app
from flowfield.errors import ApplicationError


@pytest.mark.parametrize("completion", ["code", "report"])
def test_done_is_permanent_and_archive_restore_preserve_delivery(tmp_path, completion):
    service, _, run = fixture(tmp_path, completion=completion, changed=completion == "code")
    service.results.process("harbor")
    if completion == "code":
        approve(service, current(service))
        service.results.process("harbor")
    store = service.workspace
    original = store.task("harbor", "work")
    result = current(service)
    saved_run = service.execution.get("harbor", run.id)
    assert original.status == "done"
    for status in ("backlog", "up_next", "in_progress", "in_review"):
        with pytest.raises(ApplicationError, match="Done is permanent"):
            with store.connection(write=True, workspace_id="harbor") as db:
                task = store._task_revision(db, "harbor", "work")
                store._save_task(
                    db, task.model_copy(update={"status": status, "revision": task.revision + 1})
                )
    for changes in (
        {"body": "Another outcome"},
        {"completion": "report" if completion == "code" else "code"},
    ):
        with pytest.raises(ApplicationError, match="Done is permanent"):
            store.edit_task(
                "harbor", "work", TaskEdit(expected_revision=original.revision, **changes)
            )
    with pytest.raises(ApplicationError):
        store.prioritize_task(
            "harbor", "work", TaskPriority(expected_revision=original.revision, status="up_next")
        )
    with pytest.raises(ApplicationError):
        store.reconcile_task(
            "harbor",
            "work",
            TaskReconcile(
                expected_revision=original.revision, completion=completion, note="Another outcome"
            ),
        )
    assert store.task("harbor", "work") == original
    for archived in (True, False):
        task = store.task("harbor", "work")
        changed = store.edit_task(
            "harbor", "work", TaskEdit(expected_revision=task.revision, archived=archived)
        )
        assert changed.status == "done" and changed.archived == archived
        assert changed.publication == original.publication
    assert current(service) == result
    assert service.execution.get("harbor", run.id) == saved_run
    assert service.execution.claim("harbor", run.base_commit, {run.base_commit: set()}) is None


def test_reopen_is_absent_from_http_cli_and_coordinator(tmp_path):
    service, _, _ = fixture(tmp_path, completion="report", changed=False)

    async def exercise():
        grant = await workspace_scope(service, "harbor")
        assert "reopen_task" not in grant.tools
        assert (await grant.call("reopen_task", {"task_id": "work"})).isError
        grant.revoke()

    asyncio.run(exercise())
    with TestClient(
        create_app(data_dir=service.workspace.directory), base_url="http://localhost"
    ) as client:
        assert (
            client.post(
                "/api/workspaces/harbor/tasks/work/reopen",
                json={"expected_revision": 1, "note": "Follow up"},
            ).status_code
            == 404
        )
        assert not any(
            path.endswith("/reopen") for path in client.get("/openapi.json").json()["paths"]
        )
    response = CliRunner().invoke(app, ["task", "reopen", "work", "--workspace", "harbor"])
    assert response.exit_code != 0 and "No such command" in response.output
