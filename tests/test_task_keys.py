"""Immutable workspace keys across repositories, concurrent creation and restart."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from project_fixtures import existing_directory
from workspace_fixtures import TEST_AGENTS

from flowfield.api import create_app
from flowfield.application import ProjectSetup, TaskCreate, TaskEdit, Workspace
from flowfield.errors import ApplicationError
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_keys_are_unique_stable_and_never_reused(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    owner = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    for name in ("core", "site"):
        spaces.adopt(owner.id, ProjectSetup(path=existing_directory(tmp_path / name)))

    def create(number):
        return store.create_task(
            owner.id,
            TaskCreate(title=f"Task {number}", project_id="core" if number % 2 else "site"),
        )

    with ThreadPoolExecutor(4) as pool:
        tasks = list(pool.map(create, range(12)))
    assert {t.key for t in tasks} == {f"T-{n}" for n in range(1, 13)}
    first = store.task(owner.id, "t-1")
    changed = store.edit_task(
        owner.id, first.id, TaskEdit(expected_revision=1, title="Renamed", archived=True)
    )
    assert changed.key == first.key
    assert store.create_task(owner.id, TaskCreate(title="Next")).key == "T-13"
    assert {revision.key for revision in store.task(owner.id, first.key).revisions} == {"T-1"}
    assert Workspace(store.directory).board(owner.id) == store.board(owner.id)
    foreign = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Other"))
    assert store.create_task(foreign.id, TaskCreate(title="Independent")).key == "T-1"
    with pytest.raises(ApplicationError, match="not found"):
        store.task(foreign.id, first.id)


def test_case_insensitive_keys_and_exact_ids_share_revision_guards(tmp_path):
    store = Workspace(tmp_path / "state")
    owner = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    first = store.create_task(owner.id, TaskCreate(id="first", title="First"))
    exact = store.create_task(owner.id, TaskCreate(id="t-1", title="Exact ID"))
    assert store.task(owner.id, "T-1").id == first.id
    assert store.task(owner.id, "t-1").id == exact.id
    dependent = store.create_task(owner.id, TaskCreate(title="Dependent", dependencies=[exact.id]))
    assert dependent.dependencies == [exact.key]
    with pytest.raises(ApplicationError, match="cycle"):
        store.edit_task(
            owner.id, exact.id, TaskEdit(expected_revision=1, dependencies=[dependent.key])
        )
    with pytest.raises(ApplicationError, match="stale"):
        store.edit_task(owner.id, first.key, TaskEdit(expected_revision=2, title="Lost"))


def test_project_prefixes_and_old_routes_are_not_supported(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        owner = client.post(
            "/api/workspaces",
            json={
                **{role: choice.model_dump() for role, choice in TEST_AGENTS.items()},
                "name": "Product",
            },
        ).json()["id"]
        path = existing_directory(tmp_path / "core")
        rejected = client.post(
            f"/api/workspaces/{owner}/projects", json={"path": path, "task_prefix": "COR"}
        )
        assert rejected.status_code == 422
        assert not (tmp_path / "core" / ".flowfield").exists()
        assert (
            client.post(f"/api/workspaces/{owner}/projects", json={"path": path}).status_code == 201
        )
        task = client.post(
            f"/api/workspaces/{owner}/tasks", json={"title": "Task", "project_id": "core"}
        ).json()
        assert task["key"] == "T-1"
        assert client.get("/api/projects/core/tasks").status_code == 404
        assert client.get(f"/api/workspaces/{owner}/milestones").status_code == 404
