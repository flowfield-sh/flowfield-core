"""One shared milestone source, project-bound tasks and frozen worker context."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from project_fixtures import existing_directory, task_request

from flowfield.agent_tools import workspace_scope
from flowfield.application import MilestoneCreate, MilestoneEdit, ProjectSetup, Workspace
from flowfield.errors import ApplicationError
from flowfield.milestones import Milestones
from flowfield.search import Search
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_shared_milestone_is_visible_to_members_and_has_one_search_source(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    other = spaces.create(WorkspaceCreate(name="Other"))
    milestones = Milestones(store)
    initial = milestones.create(
        space.id, MilestoneCreate(title="Orchid launch", body="Shared intent")
    )
    assert initial.workspace_id == space.id and "project_id" not in initial.model_dump()
    for identity, owner in (("core", space), ("site", space), ("elsewhere", other)):
        spaces.adopt(owner.id, ProjectSetup(path=existing_directory(tmp_path / identity)))
    for project in ("core", "site"):
        task = store.create_task(project, task_request(title="Component", milestone_id=initial.key))
        assert task.milestone_id == initial.id and task.project_id == project
        assert store.milestones(project) == [initial]
        hit = Search(store).page(project, "Orchid")["items"]
        assert len(hit) == 1 and hit[0]["identity"] == initial.id
    assert Search(store).page("elsewhere", "Orchid")["items"] == []
    with pytest.raises(ApplicationError, match="not found"):
        store.create_task("elsewhere", task_request(title="Foreign", milestone_id=initial.id))
    revised = milestones.edit(
        space.id, initial.id, MilestoneEdit(expected_revision=1, title="Maple launch")
    )
    assert not Search(store).page("core", "Orchid")["items"]
    assert len(Search(store).page("site", "Maple")["items"]) == 1
    with store.connection() as db:
        rows = db.execute(
            "SELECT project_id,workspace_id FROM search_docs WHERE entity='milestone'"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(None, space.id)]
    with ThreadPoolExecutor(4) as pool:
        created = list(
            pool.map(
                lambda index: milestones.create(space.id, MilestoneCreate(title=f"Group {index}")),
                range(8),
            )
        )
    assert {item.key for item in created} == {f"M-{index}" for index in range(2, 10)}
    assert Milestones(Workspace(store.directory)).get(space.id, initial.key) == revised


def test_workspace_tools_work_before_adoption_and_guard_full_text(tmp_path):
    store = Workspace(tmp_path / "state")
    space = Workspaces(store).create(WorkspaceCreate(name="Product"))

    async def check():
        grant = await workspace_scope(Supervisor(store), space.id, author="coordinator:fixture")
        created = await grant.call(
            "create_milestone", {"milestone": {"title": "Launch", "body": "🦉" * 9000}}
        )
        assert not created.isError
        value = created.structuredContent
        assert value["workspace_id"] == space.id and value["updated_by"] == "coordinator:fixture"
        text = await grant.call("get_milestone_text", {"identity": value["id"], "revision": 1})
        assert (
            len(text.structuredContent["text"]) == 4000
            and text.structuredContent["next_offset"] == 4000
        )
        changed = await grant.call(
            "edit_milestone",
            {"milestone_id": value["id"], "changes": {"expected_revision": 1, "body": "Updated"}},
        )
        assert not changed.isError
        historical = await grant.call(
            "get_milestone_text", {"identity": value["id"], "revision": 1}
        )
        assert not historical.isError and historical.structuredContent["text"] == "🦉" * 4000
        assert (
            await grant.call(
                "get_milestone", {"workspace_id": "elsewhere", "milestone_id": value["id"]}
            )
        ).isError
        grant.revoke()

    asyncio.run(check())
