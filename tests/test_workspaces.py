"""Workspace identity/adoption and upgrades preserve independent repository histories."""

import sqlite3
from contextlib import closing

import pytest
from project_fixtures import existing_directory, task_request

from flowfield import migrations, storage
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, Workspace
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.migrations import Migration
from flowfield.workspaces import WorkspaceCreate, WorkspaceEdit, Workspaces


def test_explicit_creation_and_revision_guard(tmp_path):
    store = Workspace(tmp_path / "state")
    workspaces = Workspaces(store)
    assert workspaces.all() == []
    assert store.projects() == []
    first = workspaces.create(WorkspaceCreate(name="Product", author="human"))
    second = workspaces.create(WorkspaceCreate(name="Product"))
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
    first = workspaces.create(WorkspaceCreate(name="Product"))
    second = workspaces.create(WorkspaceCreate(name="Other"))
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
    with pytest.raises(ApplicationError, match="different task prefix"):
        workspaces.adopt(first.id, ProjectSetup(path=str(root), task_prefix="BAD"))
    assert workspaces.projects(first.id) == [project]


def test_one_workspace_can_adopt_multiple_projects_without_creating_directories(tmp_path):
    store = Workspace(tmp_path / "state")
    workspaces = Workspaces(store)
    space = workspaces.create(WorkspaceCreate(name="Product"))
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
    space = workspaces.create(WorkspaceCreate(name="Product"))
    root = tmp_path / "core"
    root.mkdir()
    with pytest.raises(ApplicationError, match="individual task"):
        workspaces.adopt(
            space.id,
            ProjectSetup(path=str(root), worker=AgentChoice(model="fixture", fast=True)),
        )
    assert workspaces.projects(space.id) == []
    assert store.projects() == []
    assert not (root / ".flowfield").exists()
    assert workspaces.get(space.id) == space


def legacy_store(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 55)
        )
        store = Workspace(tmp_path / "state")
        for name in ("core", "site"):
            store.setup_project(
                ProjectSetup(path=existing_directory(tmp_path / name), name="X" * 200)
            )
            store.create_task(name, task_request(title="Keep task", id="work"))
            CoordinatorStore(store).new(name)
        return store


def preserved_rows(store):
    with closing(storage.connect(store.database)) as db:
        return {
            table: db.execute(f"SELECT * FROM {table}").fetchall()
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


def test_upgrade_creates_one_workspace_per_project_without_rewriting_evidence(
    tmp_path, monkeypatch
):
    old = legacy_store(tmp_path, monkeypatch)
    before = preserved_rows(old)
    store = Workspace(old.directory)
    workspaces = Workspaces(store)
    assert len(workspaces.all()) == 2
    for project in store.projects():
        space = workspaces.for_project(project.id)
        assert space.name == project.name + " Workspace"
        assert workspaces.projects(space.id) == [project]
    assert preserved_rows(store) == before
    assert len(storage.backups(store.directory)) == 1
    Workspace(store.directory)
    assert preserved_rows(store) == before
    assert len(Workspaces(store).all()) == 2
    assert len(storage.backups(store.directory)) == 1


def test_failed_workspace_upgrade_rolls_back_membership_and_retries(tmp_path, monkeypatch):
    old = legacy_store(tmp_path, monkeypatch)
    before = preserved_rows(old)
    original = next(m for m in migrations.MIGRATIONS if m.version == 55)

    def fail(db):
        original.apply(db)
        raise ValueError("workspace membership failure")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(55, fail) if m.version == 55 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back.*workspace membership failure"):
            Workspace(old.directory)
    with closing(storage.connect(old.database)) as db:
        assert storage.version(db) == 54
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='workspaces'").fetchone() is None
        )
    assert preserved_rows(old) == before
    assert len(Workspaces(Workspace(old.directory)).all()) == 2
