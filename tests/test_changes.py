import asyncio
import json
from pathlib import Path

import pytest
from project_fixtures import existing_directory, task_request

from flowfield.application import ProjectSetup, TaskEdit, Workspace
from flowfield.changes import Changes
from flowfield.errors import ApplicationError
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_change_scopes_coalesce_and_reconnect() -> None:
    async def check() -> None:
        changes = Changes()
        events = changes.events()
        assert json.loads(str((await anext(events))["data"])) == {"projects": None}
        changes.publish("alpha")
        changes.publish("beta")
        changes.publish("alpha")
        await asyncio.sleep(0)
        assert json.loads(str((await anext(events))["data"])) == {"projects": ["alpha", "beta"]}
        changes.publish("alpha")
        changes.publish()
        changes.publish("beta")
        await asyncio.sleep(0)
        assert json.loads(str((await anext(events))["data"])) == {"projects": None}
        await events.aclose()
        assert not changes.subscribers
        reconnected = changes.events()
        assert json.loads(str((await anext(reconnected))["data"])) == {"projects": None}
        await reconnected.aclose()

    asyncio.run(check())


def test_only_committed_writes_publish_project(tmp_path: Path) -> None:
    published: list[str | None] = []
    workspace = Workspace(tmp_path / "state", on_change=published.append)
    spaces = Workspaces(workspace)
    space = spaces.create(WorkspaceCreate(name="Product"))
    published.clear()
    project = spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / "repo")))
    assert published == [None]
    published.clear()
    task = workspace.create_task(project.id, task_request(title="Task"))
    assert published == [project.id]
    published.clear()
    with pytest.raises(ApplicationError):
        workspace.edit_task(project.id, task.id, TaskEdit(expected_revision=999, title="Stale"))
    assert not published


def test_activity_updates_are_scoped_coalesced_and_bounded():
    async def check():
        changes = Changes()
        events = changes.events()
        await anext(events)
        for _ in range(20):
            changes.publish_activity("alpha", "attempt")
        await asyncio.sleep(0)
        event = await anext(events)
        assert event["event"] == "activity"
        assert json.loads(event["data"]) == {"activity": [["alpha", "attempt"]]}
        for i in range(101):
            changes.publish_activity("alpha", str(i))
        await asyncio.sleep(0)
        assert json.loads((await anext(events))["data"]) == {"projects": None}
        await events.aclose()
        assert not changes.subscribers

    asyncio.run(check())


def test_coordinator_updates_do_not_impersonate_project_activity():
    async def check():
        changes = Changes()
        events = changes.events()
        await anext(events)
        changes.publish_activity("same-id", "worker")
        changes.publish_coordinator_activity("same-id", "coordinator")
        await asyncio.sleep(0)
        event = await anext(events)
        assert json.loads(event["data"]) == {
            "activity": [["same-id", "worker"]],
            "coordinator_activity": [["same-id", "coordinator"]],
        }
        for index in range(101):
            changes.publish_coordinator_activity("workspace", str(index))
        await asyncio.sleep(0)
        assert json.loads((await anext(events))["data"]) == {"projects": None}
        await events.aclose()

    asyncio.run(check())
