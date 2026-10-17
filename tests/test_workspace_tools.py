"""The coordinator can plan across member projects without implicit destinations."""

import asyncio

from agent_tools_fixtures import journey
from project_fixtures import existing_directory

from flowfield.agent_tools import workspace_scope
from flowfield.application import ProjectSetup, Workspace
from flowfield.mcp import create_mcp
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_workspace_grant_over_native_mcp_and_shared_queue(tmp_path, monkeypatch):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    service = Supervisor(store)

    async def validate(choice, project_id=None, *, project_path=None):
        assert choice.model == "fixture" and project_id is None
        assert project_path == str(store.directory / "coordinators" / space.id)

    monkeypatch.setattr(service, "validate_agent_choice", validate)

    async def exercise():
        grant = await workspace_scope(service, space.id)
        assert {"get_workers", "configure_workers", "set_queue"}.isdisjoint(grant.tools)
        assert {"milestone", "coordinator"}.isdisjoint(
            grant.tools["get_text"].inputSchema["properties"]["resource"]["enum"]
        )
        configured = await grant.call(
            "configure_workspace_workers",
            {
                "settings": {
                    "expected_revision": 1,
                    "selection": {"model": "fixture"},
                    "max_parallel": 2,
                },
            },
        )
        assert not configured.isError, configured
        current = spaces.workers(space.id)
        assert current.max_parallel == 2 and not current.enabled
        started = await grant.call(
            "set_workspace_queue",
            {
                "change": {"expected_revision": current.revision, "enabled": True},
            },
        )
        assert not started.isError
        stale = await grant.call(
            "set_workspace_queue",
            {
                "change": {"expected_revision": current.revision, "enabled": False},
            },
        )
        assert stale.isError and spaces.workers(space.id).enabled
        for name in ("core", "site"):
            spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / name)))
            assert service.execution.settings(name).enabled
        events = await journey(
            tmp_path,
            grant,
            [
                {"name": "list_workspace_projects"},
                {"name": "get_project", "arguments": {"project_id": "site"}},
                {"name": "get_project"},
            ],
        )
        results = [event["result"] for event in events[1:]]
        assert not results[0]["isError"] and not results[1]["isError"]
        assert results[2]["isError"]

    asyncio.run(exercise())


def test_workspace_description_reads_are_bounded_and_revision_checked(tmp_path):
    from flowfield.workspaces import WorkspaceEdit

    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    body = "\U0001f33f" * 10000
    spaces.edit(space.id, WorkspaceEdit(expected_revision=1, description=body))

    async def exercise():
        grant = await workspace_scope(Supervisor(store), space.id)
        brief = (await grant.call("get_workspace", {})).structuredContent
        assert "description" in brief["truncated_fields"]
        chunks, offset = [], 0
        while offset is not None:
            result = await grant.call("get_workspace_text", {"revision": 2, "offset": offset})
            assert not result.isError
            chunks.append(result.structuredContent["text"])
            offset = result.structuredContent["next_offset"]
        assert "".join(chunks) == body
        spaces.edit(space.id, WorkspaceEdit(expected_revision=2, description="Changed"))
        assert (await grant.call("get_workspace_text", {"revision": 2})).isError

    asyncio.run(exercise())


def test_workspace_read_exposes_its_reviewer_defaults_without_configuration_authority(tmp_path):
    from flowfield.agent_models import AgentChoice
    from flowfield.delivery_models import WorkspaceIntegrationPolicyEdit
    from flowfield.milestone_delivery import MilestoneDeliveries
    from flowfield.reviewer_settings import ReviewerEdit, Reviewers

    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    other = spaces.create(WorkspaceCreate(name="Other"))
    reviewers = Reviewers(store)
    reviewers.configure(
        space.id,
        "task",
        ReviewerEdit(expected_revision=1, enabled=True, selection=AgentChoice(model="fixture")),
    )
    reviewers.configure(other.id, "milestone", ReviewerEdit(expected_revision=1, enabled=False))
    policies = MilestoneDeliveries(store)
    policies.configure_workspace_policy(
        space.id, WorkspaceIntegrationPolicyEdit(expected_revision=1, enabled=True)
    )

    async def exercise():
        service = Supervisor(store)
        grant = await workspace_scope(service, space.id)
        canonical = create_mcp(lambda: store, lambda: service)
        scoped = (await grant.call("get_workspace", {})).structuredContent
        shared = await canonical.call_tool("get_workspace", {"workspace_id": space.id})
        assert shared.structuredContent["reviewers"] == scoped["reviewers"]
        assert scoped["reviewers"]["task"]["selection"]["model"] == "fixture"
        assert scoped["reviewers"]["task"]["revision"] == 2
        assert scoped["reviewers"]["milestone"]["enabled"]
        assert scoped["reviewers"]["milestone"]["selection"] is None
        assert "configure_reviewers" not in grant.tools
        assert reviewers.get(other.id, "milestone").enabled is False
        assert shared.structuredContent["integration_policy"] == scoped["integration_policy"]
        assert scoped["integration_policy"]["enabled"]
        assert scoped["integration_policy"]["revision"] == 2
        assert not policies.workspace_policy(other.id).enabled
        assert "configure_workspace_integration_policy" not in grant.tools

    asyncio.run(exercise())


def test_workspace_tools_before_adoption_and_explicit_cross_project_planning(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    service = Supervisor(store)
    other = spaces.create(WorkspaceCreate(name="Other"))
    spaces.adopt(other.id, ProjectSetup(path=existing_directory(tmp_path / "elsewhere")))

    async def exercise():
        grant = await workspace_scope(service, space.id, author="coordinator:fixture-turn")
        canonical = create_mcp(lambda: store, lambda: service, include_service_tools=False)
        assert set(grant.tools) == {tool.name for tool in await canonical.list_tools()}
        assert "initialize_project" not in grant.tools and "list_projects" not in grant.tools
        assert "workspace_id" not in grant.tools["get_workspace"].inputSchema["properties"]
        assert "project_id" in grant.tools["create_task"].inputSchema["required"]
        assert (await grant.call("list_workspace_projects", {})).structuredContent["items"] == []
        assert not (await grant.call("get_workspace_workers", {})).isError
        assert (await grant.call("get_project", {})).structuredContent["error"][
            "code"
        ] == "project_required"
        denied = await grant.call("get_workspace", {"workspace_id": other.id})
        assert denied.structuredContent["error"]["code"] == "scope_mismatch"
        for name in ("core", "site"):
            path = existing_directory(tmp_path / name)
            adopted = await grant.call(
                "adopt_workspace_project",
                {
                    "project": {"path": path, "author": "human"},
                },
            )
            assert not adopted.isError, adopted
            assert store.project(name).updated_by == "coordinator:fixture-turn"
            created = await grant.call(
                "create_task",
                {
                    "project_id": name,
                    "task": {
                        "id": "same-id",
                        "title": f"Work in {name}",
                        "body": "Agreed work",
                        "author": "human",
                        "stages": [{"id": "report", "title": "Report", "outcome": "Findings"}],
                    },
                },
            )
            assert not created.isError, created
            assert store.task(name, "same-id").updated_by == "coordinator:fixture-turn"
        first = (await grant.call("list_workspace_projects", {"limit": 1})).structuredContent
        assert [item["id"] for item in first["items"]] == ["core"]
        second = (
            await grant.call("list_workspace_projects", {"after": first["next_cursor"]})
        ).structuredContent
        assert [item["id"] for item in second["items"]] == ["site"]
        assert second["next_cursor"] is None
        assert (await grant.call("list_workspace_projects", {"limit": 51})).isError
        for name in ("elsewhere", "missing", ""):
            denied = await grant.call("get_project", {"project_id": name})
            assert denied.isError
        changed = await grant.call(
            "edit_workspace",
            {
                "changes": {
                    "expected_revision": 1,
                    "description": "Shared intent",
                    "author": "human",
                },
            },
        )
        assert not changed.isError
        assert spaces.get(space.id).updated_by == "coordinator:fixture-turn"
        # A new project cannot rewrite shared defaults through its adoption input.
        bad = await grant.call(
            "adopt_workspace_project",
            {
                "project": {
                    "path": existing_directory(tmp_path / "extra"),
                    "worker": {"model": "x"},
                },
            },
        )
        assert bad.isError
        assert len(spaces.projects(space.id)) == 2
        grant.revoke()
        assert (await grant.call("get_workspace", {})).structuredContent["error"][
            "code"
        ] == "scope_closed"

    asyncio.run(exercise())


def test_workspace_adoption_cannot_steal_project_and_scope_liveness_is_checked(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    first = spaces.create(WorkspaceCreate(name="First"))
    second = spaces.create(WorkspaceCreate(name="Second"))
    path = existing_directory(tmp_path / "core")
    spaces.adopt(first.id, ProjectSetup(path=path))
    checked = []

    async def exercise():
        grant = await workspace_scope(
            Supervisor(store), second.id, check=lambda: checked.append(True)
        )
        stolen = await grant.call("adopt_workspace_project", {"project": {"path": path}})
        assert stolen.isError
        assert checked == [True]
        assert spaces.for_project("core").id == first.id
        denied = await grant.call(
            "edit_project",
            {
                "project_id": "core",
                "changes": {"expected_revision": 1, "description": "Wrong"},
            },
        )
        assert denied.structuredContent["error"]["code"] == "scope_mismatch"
        assert store.project("core").description == ""

    asyncio.run(exercise())
