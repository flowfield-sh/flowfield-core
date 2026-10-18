"""Shared coordinator preferences never borrow a repository's mutable settings."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from coordinator_generations_fixtures import CLAUDE, CODEX
from project_fixtures import existing_directory
from pydantic import ValidationError
from workspace_fixtures import TEST_AGENTS

from flowfield.agent_models import AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.application import ProjectSetup, Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_empty_workspace_selection_shared_by_adopted_projects_and_guarded(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    assert spaces.coordinator(space.id).effective.choice == TEST_AGENTS["coordinator"]
    selected = spaces.configure_coordinator(
        space.id, AgentSettingsEdit(expected_revision=1, selection=CODEX)
    )
    assert selected.effective.source == "workspace"
    for name in ("core", "site"):
        spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / name)))
        assert AgentSettings(store).get(space.id, "coordinator") == selected
    changed = AgentSettings(store).edit(
        space.id,
        "coordinator",
        AgentSettingsEdit(expected_revision=selected.revision, selection=CLAUDE),
    )
    assert AgentSettings(store).get(space.id, "coordinator") == changed
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
    other = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Other"))
    assert spaces.coordinator(other.id).selection == TEST_AGENTS["coordinator"]


def test_shared_choice_change_observes_active_sibling_and_keeps_frozen_turn(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    for name in ("core", "site"):
        spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / name)))
    spaces.configure_coordinator(space.id, AgentSettingsEdit(expected_revision=1, selection=CODEX))
    owner = CoordinatorStore(store)
    turn, _ = owner.reserve(
        space.id, owner.new(space.id).id, CoordinatorSend(id=uuid4().hex, text="Plan work")
    )
    with pytest.raises(ApplicationError, match="confirm cleanup"):
        AgentSettings(store).edit(
            space.id, "coordinator", AgentSettingsEdit(expected_revision=2, selection=CLAUDE)
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
    space = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    with pytest.raises(ValidationError, match="Extra inputs"):
        spaces.adopt(
            space.id, ProjectSetup(path=existing_directory(tmp_path / "core"), coordinator=CODEX)
        )
    assert spaces.projects(space.id) == []
    assert spaces.coordinator(space.id).revision == 1


def test_competing_workspace_and_project_settings_edits_have_one_winner(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / "core")))

    def save(project_view):
        try:
            request = AgentSettingsEdit(
                expected_revision=1, selection=CODEX if project_view else CLAUDE
            )
            if project_view:
                return AgentSettings(store).edit(space.id, "coordinator", request).revision
            return spaces.configure_coordinator(space.id, request).revision
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, (True, False)))
    assert outcomes.count(2) == 1 and outcomes.count("revision_conflict") == 1
    assert AgentSettings(store).get(space.id, "coordinator") == spaces.coordinator(space.id)
