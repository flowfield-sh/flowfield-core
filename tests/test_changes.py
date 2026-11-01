import asyncio
import json
from pathlib import Path

import pytest
from project_fixtures import existing_directory, task_request
from workspace_fixtures import TEST_AGENTS

from flowfield.agent_tools import workspace_scope
from flowfield.application import ProjectSetup, TaskEdit, Workspace
from flowfield.changes import Changes
from flowfield.errors import ApplicationError
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_change_scopes_coalesce_and_reconnect() -> None:
    async def check() -> None:
        changes = Changes()
        events = changes.events()
        assert json.loads(str((await anext(events))["data"])) == {"workspaces": None}
        changes.publish("alpha")
        changes.publish("beta")
        changes.publish("alpha")
        await asyncio.sleep(0)
        assert json.loads(str((await anext(events))["data"])) == {"workspaces": ["alpha", "beta"]}
        changes.publish("alpha")
        changes.publish()
        changes.publish("beta")
        await asyncio.sleep(0)
        assert json.loads(str((await anext(events))["data"])) == {"workspaces": None}
        await events.aclose()
        assert not changes.subscribers
        reconnected = changes.events()
        assert json.loads(str((await anext(reconnected))["data"])) == {"workspaces": None}
        await reconnected.aclose()

    asyncio.run(check())


def test_only_committed_writes_publish_project(tmp_path: Path) -> None:
    published: list[str | None] = []
    workspace = Workspace(tmp_path / "state", on_change=published.append)
    spaces = Workspaces(workspace)
    space = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    published.clear()
    spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / "repo")))
    assert published == [None]
    published.clear()
    task = workspace.create_task(space.id, task_request(title="Task"))
    assert published == [space.id]
    published.clear()
    with pytest.raises(ApplicationError):
        workspace.edit_task(space.id, task.id, TaskEdit(expected_revision=999, title="Stale"))
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
        assert json.loads((await anext(events))["data"]) == {"workspaces": None}
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
        assert json.loads((await anext(events))["data"]) == {"workspaces": None}
        await events.aclose()

    asyncio.run(check())


def test_coordinator_task_creation_publishes_change_during_activity(tmp_path):
    async def check():
        changes = Changes()
        workspace = Workspace(tmp_path / "state", on_change=changes.publish)
        space = Workspaces(workspace).create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
        grant = await workspace_scope(Supervisor(workspace), space.id)
        await asyncio.sleep(0)
        events = changes.events()
        await anext(events)
        changes.publish_coordinator_activity(space.id, "turn")
        result = await grant.call("create_task", {"task": {"title": "Created in chat"}})
        assert not result.isError, result
        changes.publish_coordinator_activity(space.id, "turn")
        await asyncio.sleep(0)
        async with asyncio.timeout(2):
            event = await anext(events)
        assert event["event"] == "change"
        assert json.loads(event["data"]) == {"workspaces": [space.id]}
        assert workspace.task(space.id, "T-1").title == "Created in chat"
        await events.aclose()
        assert not changes.subscribers

    asyncio.run(check())


def test_usage_bursts_stay_scoped_without_general_invalidations():
    async def check():
        changes = Changes()
        events = changes.events()
        assert (await anext(events))["event"] == "change"
        for _ in range(100):
            changes.publish_usage("alpha")
        changes.publish_usage("beta")
        async with asyncio.timeout(2):
            event = await anext(events)
        assert event["event"] == "usage"
        assert json.loads(event["data"]) == {"workspaces": ["alpha", "beta"]}
        for index in range(101):
            changes.publish_usage(str(index))
        async with asyncio.timeout(2):
            event = await anext(events)
        assert event["event"] == "usage"
        assert json.loads(event["data"]) == {"workspaces": None}
        changes.publish_usage("alpha")
        changes.publish("beta")
        assert (await anext(events))["event"] == "change"
        assert (await anext(events))["event"] == "usage"
        await events.aclose()
        reconnected = changes.events()
        assert (await anext(reconnected))["event"] == "change"
        await reconnected.aclose()

    asyncio.run(check())


def test_usage_only_publishes_after_commit_without_attention_reconciliation(tmp_path, monkeypatch):
    from usage_fixtures import accounting, event

    execution, usage, (run,) = accounting(tmp_path)
    workspace = execution.workspace
    general, usage_events = [], []
    workspace.on_change = general.append

    def received(identity):
        with workspace.connection() as db:
            assert db.execute("SELECT count(*) FROM usage_observations").fetchone()[0] == 1
        usage_events.append(identity)

    workspace.on_usage = received

    def unexpected(*args):
        pytest.fail("Usage writes must not reconcile workspace attention")

    monkeypatch.setattr("flowfield.announcements.reconcile_attention", unexpected)
    observation = event(run)
    usage.observe("harbor", "run", run.id, observation)
    usage.observe("harbor", "run", run.id, observation)
    assert usage_events == ["harbor"] and general == []
    with workspace.connection(write=True, notify=False) as db:
        db.execute(
            "CREATE TRIGGER abort_receipt BEFORE INSERT ON usage_observations "
            "BEGIN SELECT RAISE(ABORT,'receipt rollback'); END"
        )
    with pytest.raises(Exception, match="receipt rollback"):
        usage.observe("harbor", "run", run.id, event(run, 200, identity="rollback"))
    assert usage_events == ["harbor"] and general == []
