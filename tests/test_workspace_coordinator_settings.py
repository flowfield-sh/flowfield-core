"""Shared coordinator preferences never borrow a repository's mutable settings."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from project_fixtures import existing_directory
from test_coordinator_generations import CLAUDE, CODEX, finish_coordinator, start_coordinator
from test_execution import fixture
from test_storage_migrations import logical_data

from flowfield import migrations
from flowfield.agent_models import AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.application import ProjectSetup, Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.migrations import Migration
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_empty_workspace_selection_shared_by_adopted_projects_and_guarded(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    assert spaces.coordinator(space.id).effective is None
    selected = spaces.configure_coordinator(
        space.id, AgentSettingsEdit(expected_revision=1, selection=CODEX)
    )
    assert selected.effective.source == "workspace"
    for name in ("core", "site"):
        spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / name)))
        assert AgentSettings(store).get(name, "coordinator") == selected
    changed = AgentSettings(store).edit(
        "site",
        "coordinator",
        AgentSettingsEdit(expected_revision=selected.revision, selection=CLAUDE),
    )
    assert AgentSettings(store).get("core", "coordinator") == changed
    assert spaces.coordinator(space.id) == changed
    with pytest.raises(ApplicationError, match="stale"):
        spaces.configure_coordinator(
            space.id, AgentSettingsEdit(expected_revision=selected.revision)
        )
    assert Workspaces(Workspace(store.directory)).coordinator(space.id) == changed
    with store.connection() as db:
        assert (
            db.execute("SELECT count(*) FROM agent_settings WHERE role='coordinator'").fetchone()[0]
            == 0
        )
        assert db.execute("SELECT count(*) FROM workspace_coordinator_settings").fetchone()[0] == 1
    other = spaces.create(WorkspaceCreate(name="Other"))
    assert spaces.coordinator(other.id).selection is None


def test_shared_choice_change_observes_active_sibling_and_keeps_frozen_turn(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    for name in ("core", "site"):
        spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / name)))
    spaces.configure_coordinator(space.id, AgentSettingsEdit(expected_revision=1, selection=CODEX))
    owner = CoordinatorStore(store)
    turn, _ = owner.reserve(
        space.id, owner.new(space.id).id, CoordinatorSend(id=uuid4().hex, text="Plan work")
    )
    with pytest.raises(ApplicationError, match="confirm cleanup"):
        AgentSettings(store).edit(
            "site", "coordinator", AgentSettingsEdit(expected_revision=2, selection=CLAUDE)
        )
    with store.connection(write=True) as db:
        turn.native_started = True
        owner._save(db, turn)
    owner.restart()
    assert owner.get(space.id, turn.id).status == "uncertain"
    with pytest.raises(ApplicationError, match="confirm cleanup"):
        spaces.configure_coordinator(
            space.id, AgentSettingsEdit(expected_revision=2, selection=CLAUDE)
        )
    owner.confirm_stopped(space.id, turn.id)
    spaces.configure_coordinator(space.id, AgentSettingsEdit(expected_revision=2, selection=CLAUDE))
    assert owner.get(space.id, turn.id).settings == turn.settings


def test_adoption_cannot_change_workspace_coordinator(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    with pytest.raises(ApplicationError, match="Configure the workspace coordinator separately"):
        spaces.adopt(
            space.id, ProjectSetup(path=existing_directory(tmp_path / "core"), coordinator=CODEX)
        )
    assert spaces.projects(space.id) == []
    assert spaces.coordinator(space.id).revision == 1


def test_competing_workspace_and_project_settings_edits_have_one_winner(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / "core")))

    def save(project_view):
        try:
            request = AgentSettingsEdit(
                expected_revision=1, selection=CODEX if project_view else CLAUDE
            )
            if project_view:
                return AgentSettings(store).edit("core", "coordinator", request).revision
            return spaces.configure_coordinator(space.id, request).revision
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, (True, False)))
    assert outcomes.count(2) == 1 and outcomes.count("revision_conflict") == 1
    assert AgentSettings(store).get("core", "coordinator") == spaces.coordinator(space.id)


def test_migration_preserves_selected_revision_frozen_turn_and_native_cleanup(
    tmp_path, monkeypatch
):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 55)
        )
        store = fixture(tmp_path).workspace
        owner, turn, source = start_coordinator(store)
        finish_coordinator(owner, turn, cleanup=False)
        with store.connection() as db:
            source = owner.sessions.get(db, "harbor", source.id)
        before = AgentSettings(store).get("harbor", "coordinator")
    upgraded = Workspace(store.directory)
    spaces = Workspaces(upgraded)
    current = spaces.coordinator("harbor")
    assert current.revision == before.revision and current.selection == before.selection
    assert current.effective.source == "workspace"
    assert CoordinatorStore(upgraded).get("harbor", turn.id).settings == turn.settings
    assert turn.settings.source == "project"
    with pytest.raises(ApplicationError, match="confirm cleanup"):
        spaces.configure_coordinator(
            "harbor", AgentSettingsEdit(expected_revision=current.revision, selection=CLAUDE)
        )
    with upgraded.connection() as db:
        assert owner.sessions.get(db, "harbor", source.id).session_id == source.session_id
        assert not db.execute("SELECT 1 FROM agent_settings WHERE role='coordinator'").fetchone()


def test_failed_coordinator_settings_migration_restores_original_rows(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 55)
        )
        store = fixture(tmp_path).workspace
        owner, turn, _ = start_coordinator(store)
        finish_coordinator(owner, turn)
    before = logical_data(store.directory)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 58)

    def fail(db):
        migration.apply(db)
        raise ValueError("fixture coordinator settings failure")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(58, fail) if m.version == 58 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    assert Workspaces(Workspace(store.directory)).coordinator("harbor").selection == CODEX
