"""Adjacent views are frozen relevance with bounded references and existing scope checks."""

import json
from uuid import uuid4

import pytest
from coordinator_fixtures import setup
from project_fixtures import existing_directory, task_request

from flowfield.application import Edit, MilestoneCreate, ProjectSetup
from flowfield.coordinator_models import (
    CoordinatorSend,
    CoordinatorTaskSelection,
    CoordinatorViewSelection,
)
from flowfield.errors import ApplicationError
from flowfield.labels import LabelEdit, Labels
from flowfield.milestones import Milestones
from flowfield.reads import ContextReads
from flowfield.workspaces import WorkspaceCreate, WorkspaceEdit, Workspaces


def test_filtered_view_is_frozen_idempotent_and_available_in_history(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    store, ws = service.coordinator.store, service.workspace
    milestone = Milestones(ws).create("harbor", MilestoneCreate(title="Shared launch"))
    selected = CoordinatorViewSelection(
        surface="board", project_id="harbor", milestone_id=milestone.id, label_id="bug"
    )
    request = CoordinatorSend(id=uuid4().hex, text="Prioritize this work", view_context=selected)
    turn, fresh = store.reserve("harbor", conversation.id, request)
    assert fresh and turn.task_context is None
    assert turn.view_context.project_name == ws.project("harbor").name
    assert turn.view_context.milestone_title == "Shared launch"
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
    foreign = spaces.create(WorkspaceCreate(name="Other workspace"))
    spaces.adopt(
        foreign.id, ProjectSetup(id="foreign", path=existing_directory(tmp_path / "foreign"))
    )
    milestone = Milestones(ws).create(foreign.id, MilestoneCreate(title="Other launch"))
    task = ws.create_task("harbor", task_request(title="The current task"))
    task_context = CoordinatorTaskSelection(
        project_id="harbor", task_id=task.id, task_revision=task.revision
    )
    for view, selected_task in [
        (CoordinatorViewSelection(surface="board", project_id="foreign"), None),
        (CoordinatorViewSelection(surface="board", milestone_id=milestone.id), None),
        (CoordinatorViewSelection(surface="board", label_id="missing"), None),
        (CoordinatorViewSelection(surface="projects", project_id="harbor"), None),
        (CoordinatorViewSelection(surface="inbox", label_id="bug"), None),
        (CoordinatorViewSelection(surface="task", project_id="harbor"), None),
        (CoordinatorViewSelection(surface="task", project_id="foreign"), task_context),
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


def test_milestone_view_retains_exact_revision_and_validates_scope(tmp_path, monkeypatch):
    from flowfield.application import MilestoneEdit

    service, conversation = setup(tmp_path, monkeypatch)
    ws, store = service.workspace, service.coordinator.store
    milestones = Milestones(ws)
    milestone = milestones.create("harbor", MilestoneCreate(title="Original outcome"))
    milestones.edit("harbor", milestone.id, MilestoneEdit(expected_revision=1, title="New outcome"))
    foreign = Workspaces(ws).create(WorkspaceCreate(name="Elsewhere"))
    other = milestones.create(foreign.id, MilestoneCreate(title="Foreign outcome"))
    for view in [
        CoordinatorViewSelection(surface="milestone", milestone_id=milestone.id),
        CoordinatorViewSelection(surface="milestone", milestone_revision=1),
        CoordinatorViewSelection(surface="milestone", milestone_id=other.id, milestone_revision=1),
        CoordinatorViewSelection(
            surface="milestone", milestone_id=milestone.id, milestone_revision=99
        ),
        CoordinatorViewSelection(surface="board", milestone_id=milestone.id, milestone_revision=1),
    ]:
        with pytest.raises(ApplicationError):
            store.reserve(
                "harbor",
                conversation.id,
                CoordinatorSend(id=uuid4().hex, text="Review", view_context=view),
            )
        assert store.page("harbor").items == []
    request = CoordinatorSend(
        id=uuid4().hex,
        text="Review this version",
        view_context=CoordinatorViewSelection(
            surface="milestone", milestone_id=milestone.id, milestone_revision=1
        ),
    )
    turn, fresh = store.reserve("harbor", conversation.id, request)
    assert fresh
    assert turn.view_context.milestone_title == "Original outcome"
    assert turn.view_context.milestone_revision == 1
    assert store.reserve("harbor", conversation.id, request) == (turn, False)
    prompt = json.loads(service.coordinator._prompt(turn, "scoped", resumed=True))
    assert prompt["adjacent_view"]["milestone_revision"] == 1
    with pytest.raises(ApplicationError) as changed:
        store.reserve(
            "harbor",
            conversation.id,
            request.model_copy(
                update={
                    "view_context": request.view_context.model_copy(
                        update={"milestone_revision": 2}
                    )
                }
            ),
        )
    assert changed.value.code == "message_conflict"
