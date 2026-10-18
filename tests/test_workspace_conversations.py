"""Workspace chat ownership and explicit project context."""

import pytest
from coordinator_fixtures import message
from coordinator_generations_fixtures import CODEX
from project_fixtures import existing_directory

from flowfield.agent_models import AgentSettingsEdit
from flowfield.application import ProjectSetup, Workspace
from flowfield.coordinator_models import CoordinatorTaskSelection
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_empty_workspace_chat_and_explicit_project_context(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    other = spaces.create(WorkspaceCreate(name="Other"))
    spaces.configure_coordinator(space.id, AgentSettingsEdit(expected_revision=1, selection=CODEX))
    owner = CoordinatorStore(store)
    conversation = owner.new(space.id)
    turn, _ = owner.reserve(space.id, conversation.id, message("Plan the workspace"))
    assert turn.workspace_id == space.id and turn.task_context is None
    owner.restart()
    assert owner.get(space.id, turn.id).status == "interrupted"
    for identity, workspace in (("core", space), ("site", space), ("outside", other)):
        spaces.adopt(
            workspace.id, ProjectSetup(id=identity, path=existing_directory(tmp_path / identity))
        )
    assert owner.new(space.id).id == conversation.id
    from project_fixtures import task_request

    for project in ("core", "site", "outside"):
        store.create_task(
            spaces.for_project(project).id,
            task_request(project_id=project, id=project, title=project),
        )
    task = store.task(space.id, "site")
    request = message("Discuss this task")
    request.task_context = CoordinatorTaskSelection(task_id=task.id, task_revision=task.revision)
    selected, _ = owner.reserve(space.id, conversation.id, request)
    assert selected.task_context.project_id == "site"
    request.task_context.task_id = "core"
    with pytest.raises(ApplicationError, match="different content"):
        owner.reserve(space.id, conversation.id, request)
    request.task_context.task_id = "outside"
    with pytest.raises(ApplicationError, match="in this workspace"):
        owner.reserve(space.id, conversation.id, request)
    with pytest.raises(ApplicationError, match="not found"):
        owner.get(other.id, selected.id)
