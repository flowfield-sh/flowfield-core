"""Adjacent views are frozen relevance with bounded references and existing scope checks."""

import json
from uuid import uuid4

import pytest
from coordinator_fixtures import setup
from project_fixtures import existing_directory, task_request
from workspace_fixtures import TEST_AGENTS

from flowfield.application import Edit, ProjectSetup
from flowfield.coordinator_models import (
    CoordinatorSend,
    CoordinatorTaskSelection,
    CoordinatorViewSelection,
)
from flowfield.errors import ApplicationError
from flowfield.labels import LabelEdit, Labels
from flowfield.reads import ContextReads
from flowfield.workspaces import WorkspaceCreate, WorkspaceEdit, Workspaces


def test_filtered_view_is_frozen_idempotent_and_available_in_history(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    store, ws = service.coordinator.store, service.workspace
    selected = CoordinatorViewSelection(surface="board", project_id="harbor", label_id="bug")
    request = CoordinatorSend(id=uuid4().hex, text="Prioritize this work", view_context=selected)
    turn, fresh = store.reserve("harbor", conversation.id, request)
    assert fresh and turn.task_context is None
    assert turn.view_context.project_name == ws.project("harbor").name
    assert turn.view_context.label_name == "Bug"
    original = turn.view_context.model_dump()
    spaces = Workspaces(ws)
    spaces.edit(
        "harbor",
        WorkspaceEdit(expected_revision=spaces.get("harbor").revision, name="Changed workspace"),
    )
    Labels(ws).edit("harbor", "bug", LabelEdit(expected_revision=1, name="Defect"))
    Labels(ws).delete("harbor", "bug", Edit(expected_revision=2))
    assert store.reserve("harbor", conversation.id, request) == (turn, False)
    assert (
        json.loads(service.coordinator._prompt(turn, "scoped", resumed=True))["adjacent_view"]
        == original
    )
    with pytest.raises(ApplicationError) as changed:
        store.reserve(
            "harbor",
            conversation.id,
            request.model_copy(
                update={"view_context": selected.model_copy(update={"project_id": None})}
            ),
        )
    assert changed.value.code == "message_conflict"
    with pytest.raises(ApplicationError) as deleted:
        store.reserve("harbor", conversation.id, request.model_copy(update={"id": uuid4().hex}))
    assert deleted.value.code == "view_context_changed"
    with ws.connection(write=True) as db:
        turn.status = "completed"
        store._save(db, turn)
    next_turn, _ = store.reserve(
        "harbor",
        conversation.id,
        CoordinatorSend(
            id=uuid4().hex,
            text="Now all work",
            view_context=CoordinatorViewSelection(surface="board"),
        ),
    )
    assert next_turn.view_context.project_id is None
    prompt = json.loads(service.coordinator._prompt(next_turn, "scoped", budget=60_000))
    assert prompt["adjacent_view"]["workspace_name"] == "Changed workspace"
    assert prompt["recent_conversation"][0]["adjacent_view"] == original
    history = ContextReads(ws).coordinator_history("harbor", before=next_turn.number)
    assert history["items"][0]["view_context"] == original
    assert "body" not in original and "guidance" not in original


def test_view_membership_and_task_identity_are_validated_before_reserving(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    ws, store = service.workspace, service.coordinator.store
    spaces = Workspaces(ws)
    foreign = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Other workspace"))
    spaces.adopt(
        foreign.id, ProjectSetup(id="foreign", path=existing_directory(tmp_path / "foreign"))
    )
    spaces.adopt(
        "harbor", ProjectSetup(id="neighbor", path=existing_directory(tmp_path / "neighbor"))
    )
    task = ws.create_task("harbor", task_request(project_id="harbor", title="The current task"))
    task_context = CoordinatorTaskSelection(task_id=task.id, task_revision=task.revision)
    for view, selected_task in [
        (CoordinatorViewSelection(surface="board", project_id="foreign"), None),
        (CoordinatorViewSelection(surface="board", label_id="missing"), None),
        (CoordinatorViewSelection(surface="projects", project_id="harbor"), None),
        (CoordinatorViewSelection(surface="inbox", label_id="bug"), None),
        (CoordinatorViewSelection(surface="task", project_id="harbor"), None),
        (CoordinatorViewSelection(surface="task", project_id="foreign"), task_context),
        (CoordinatorViewSelection(surface="task", project_id="neighbor"), task_context),
        (CoordinatorViewSelection(surface="board"), task_context),
    ]:
        with pytest.raises(ApplicationError):
            store.reserve(
                "harbor",
                conversation.id,
                CoordinatorSend(
                    id=uuid4().hex, text="Check", view_context=view, task_context=selected_task
                ),
            )
        assert store.page("harbor").items == []
    turn, _ = store.reserve(
        "harbor",
        conversation.id,
        CoordinatorSend(
            id=uuid4().hex,
            text="Explain this task",
            view_context=CoordinatorViewSelection(surface="task", project_id="harbor"),
            task_context=task_context,
        ),
    )
    assert turn.task_context.key == task.key
    assert turn.view_context.project_id == task.project_id
    history = ContextReads(ws).coordinator_history("harbor")
    assert history["items"][0]["task_context"]["key"] == task.key
