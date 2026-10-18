"""Workspace identity/adoption and upgrades preserve independent repository histories."""

import sqlite3
from contextlib import closing

import pytest
from project_fixtures import existing_directory
from pydantic import ValidationError
from workspace_fixtures import TEST_AGENTS

from flowfield import storage
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, Workspace
from flowfield.errors import ApplicationError
from flowfield.workspaces import WorkspaceCreate, WorkspaceEdit, Workspaces


def test_explicit_creation_and_revision_guard(tmp_path):
    store = Workspace(tmp_path / "state")
    workspaces = Workspaces(store)
    assert workspaces.all() == []
    assert store.projects() == []
    first = workspaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product", author="human"))
    second = workspaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    assert first.id != second.id
    assert workspaces.projects(first.id) == []
    updated = workspaces.edit(
        first.id,
        WorkspaceEdit(expected_revision=1, name="Product development", description="Shared intent"),
    )
    assert updated.revision == 2
    assert updated.name == "Product development"
    assert workspaces.get(second.id) == second
    with pytest.raises(ApplicationError, match="stale"):
        workspaces.edit(first.id, WorkspaceEdit(expected_revision=1, name="Stale"))
    assert Workspaces(Workspace(store.directory)).get(first.id) == updated


def test_project_adoption_is_scoped_atomic_and_preserves_files(tmp_path):
    store = Workspace(tmp_path / "state")
    workspaces = Workspaces(store)
    first = workspaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    second = workspaces.create(WorkspaceCreate(**TEST_AGENTS, name="Other"))
    root = tmp_path / "core"
    root.mkdir()
    (root / "AGENTS.md").write_text("Keep repository guidance")
    request = ProjectSetup(path=str(root))
    with pytest.raises(ApplicationError, match="Workspace not found"):
        workspaces.adopt("missing", request)
    assert not (root / ".flowfield").exists()
    project = workspaces.adopt(first.id, request)
    assert workspaces.adopt(first.id, request) == project
    assert workspaces.for_project(project.id) == first
    assert workspaces.projects(first.id) == [project]
    assert workspaces.projects(second.id) == []
    with pytest.raises(ApplicationError, match="another workspace"):
        workspaces.adopt(second.id, request)
    assert workspaces.for_project(project.id) == first
    assert (root / "AGENTS.md").read_text() == "Keep repository guidance"
    with pytest.raises(ValidationError, match="Extra inputs"):
        workspaces.adopt(first.id, ProjectSetup(path=str(root), task_prefix="BAD"))
    assert workspaces.projects(first.id) == [project]


def test_one_workspace_can_adopt_multiple_projects_without_creating_directories(tmp_path):
    store = Workspace(tmp_path / "state")
    workspaces = Workspaces(store)
    space = workspaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    for name in ("core", "site"):
        workspaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / name)))
    assert [project.id for project in workspaces.projects(space.id)] == ["core", "site"]
    with pytest.raises(ApplicationError, match="existing project directory"):
        workspaces.adopt(space.id, ProjectSetup(path=str(tmp_path / "absent")))
    assert not (tmp_path / "absent").exists()
    with store.connection(write=True) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO workspace_projects VALUES ('missing', ?)", (space.id,))


def test_failed_project_configuration_rolls_back_membership(tmp_path):
    store = Workspace(tmp_path / "state")
    workspaces = Workspaces(store)
    space = workspaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    root = tmp_path / "core"
    root.mkdir()
    with pytest.raises(ValidationError, match="Extra inputs"):
        workspaces.adopt(
            space.id,
            ProjectSetup(path=str(root), worker=AgentChoice(model="fixture", fast=True)),
        )
    assert workspaces.projects(space.id) == []
    assert store.projects() == []
    assert not (root / ".flowfield").exists()
    assert workspaces.get(space.id) == space


def preserved_rows(store):
    with closing(storage.connect(store.database)) as db:
        return {
            table: db.execute(
                "SELECT id,name,path,description,revision,created_at,updated_at,updated_by,"
                "task_prefix FROM projects"
                if table == "projects"
                else f"SELECT * FROM {table}"
            ).fetchall()
            for table in (
                "projects",
                "tasks",
                "task_revisions",
                "activity",
                "worker_settings",
                "coordinator_conversations",
                "coordinator_turns",
                "coordinator_sessions",
            )
        }
