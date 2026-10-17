from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt, task_request

from flowfield.api import create_app
from flowfield.application import ProjectSetup, TaskEdit, TaskProgress, Workspace
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError


def test_batched_cards_preserve_question_scope_state_and_refresh(tmp_path):
    from execution_fixtures import BASE, fixture

    from flowfield.execution_models import QueueEdit
    from flowfield.questions import QuestionCreate, Questions

    execution = fixture(tmp_path, count=3)
    store = execution.workspace
    run = execution.claim("harbor", BASE, {BASE: set()}, task_id="task-0")
    execution.started("harbor", run.id)
    questions = Questions(store)
    shared = questions.ask(
        "harbor",
        QuestionCreate(
            affected_task_ids=["task-1", "task-2"],
            blocking_scope="implementation",
            question="Choose the format",
            context="Both tasks need a shared format.",
            recommendation="Use CSV",
        ),
    )
    direct = questions.ask(
        "harbor",
        QuestionCreate(
            task_id="task-1",
            blocking_scope="implementation",
            question="Choose a filename",
            context="Name the output.",
            recommendation="result.csv",
        ),
    )
    other = adopt(store, ProjectSetup(path=str(tmp_path / "other"), task_prefix="OTH"))
    store.create_task(other.id, task_request(id="task-1", title="Unrelated task"))
    questions.ask(
        other.id,
        QuestionCreate(
            task_id="task-1",
            blocking_scope="implementation",
            question="Other scope",
            context="Another project",
            recommendation="Keep isolated",
        ),
    )
    reads = BrowserReads(store)
    board = reads.board("harbor")
    cards = {card.id: card for card in board.tasks}
    assert cards["task-0"].state.label == "Working"
    assert cards["task-1"].state.label == "Needs your answer"
    assert cards["task-2"].state.label == "Queued"
    assert [q.id for q in cards["task-1"].blocking_questions] == [shared.id, direct.id]
    assert [q.id for q in cards["task-2"].blocking_questions] == [shared.id]
    assert cards["task-2"].readiness == "blocked"
    with store.connection() as db:
        workspace = store._project_workspace(db, "harbor")
    combined = reads.workspace_board(workspace)
    assert combined.tasks == board.tasks
    for card in board.tasks:
        detail = reads.task("harbor", card.id)
        assert detail.model_dump(include=set(type(card).model_fields)) == card.model_dump()
    settings = execution.settings("harbor")
    execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    refreshed = {card.id: card for card in reads.workspace_board(workspace).tasks}
    assert refreshed["task-2"].state.label == "Queue paused"


def test_cards_and_current_details_never_hydrate_historical_text(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "state")
    project = adopt(workspace, ProjectSetup(path=str(tmp_path / "repo")))
    task = workspace.create_task(
        project.id, task_request(title="Large description", body="x" * 200_000)
    )
    for index in range(6):
        task = workspace.edit_task(
            project.id,
            task.id,
            TaskEdit(
                expected_revision=task.revision,
                body=str(index) * 200_000,
            ),
        )
    blocked = workspace.create_task(
        project.id, task_request(title="Blocked", dependencies=[task.id])
    )
    task = workspace.edit_task(
        project.id, task.id, TaskEdit(expected_revision=task.revision, archived=True)
    )
    archived_at = task.archived_at
    task = workspace.edit_task(
        project.id, task.id, TaskEdit(expected_revision=task.revision, title="Edited after archive")
    )
    full = workspace.board(project.id)
    reads = BrowserReads(workspace)
    # Browser projections must not delegate to the full record/history reader.
    with patch.object(workspace, "_task", side_effect=AssertionError("Full task hydrated")):
        board = reads.board(project.id)
        assert len(board.model_dump_json()) < 5000
        assert len(full.model_dump_json()) > 1_000_000
        for card in board.tasks:
            original = next(t for t in full.tasks if t.id == card.id)
            assert card.state is not None
            assert card.model_dump(exclude={"state"}) == {
                key: original.model_dump()[key] for key in card.model_dump() if key != "state"
            }
        detail = reads.task(project.id, task.key)
        assert detail.body == task.body
        assert "revisions" not in detail.model_dump()
        assert detail.archived_at == archived_at
        assert detail.dependents[0].id == blocked.id
        assert reads.revision(project.id, task.key, 1).body == "x" * 200_000
        with pytest.raises(ApplicationError, match="revision not found"):
            reads.revision(project.id, task.id, 999)


def test_state_entry_and_rearchive_metadata_match_full_records(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "state")
    project = adopt(workspace, ProjectSetup(path=str(tmp_path / "repo")))
    task = workspace.create_task(project.id, task_request(title="Task"))
    task = workspace.record_progress(
        project.id,
        task.id,
        TaskProgress(expected_revision=task.revision, status="done", completion="report"),
    )
    entered = task.status_changed_at
    for archived in [True, False, True]:
        task = workspace.edit_task(
            project.id, task.id, TaskEdit(expected_revision=task.revision, archived=archived)
        )
        card = BrowserReads(workspace).board(project.id).tasks[0]
        assert card.status_changed_at == entered
        assert card.archived_at == task.archived_at
        assert card.archived_by == task.archived_by
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1") as client:
        assert client.get(f"/api/projects/{project.id}/view/board").status_code == 200
        detail = client.get(f"/api/projects/{project.id}/view/tasks/{task.key}").json()
        assert "body" in detail and "revisions" not in detail
        edited = client.put(
            f"/api/projects/{project.id}/view/tasks/{task.key}",
            json={
                "expected_revision": task.revision,
                "title": "Browser edit",
            },
        )
        assert edited.status_code == 200
        assert edited.json()["revision"] == task.revision + 1
        assert "revisions" not in edited.json()
        assert (
            client.get(f"/api/projects/{project.id}/view/tasks/{task.key}/revisions/1").json()[
                "revision"
            ]
            == 1
        )
        assert (
            client.get(
                f"/api/projects/{project.id}/view/tasks/{task.key}/revisions/999"
            ).status_code
            == 404
        )


def test_archive_availability_shares_write_policy_and_rechecks_races(tmp_path: Path) -> None:
    from flowfield.questions import QuestionCreate, Questions

    workspace = Workspace(tmp_path / "state")
    project = adopt(workspace, ProjectSetup(path=str(tmp_path / "repo")))
    task = workspace.create_task(project.id, task_request(title="Archive safely"))
    reads = BrowserReads(workspace)
    assert reads.task(project.id, task.id).archive_blocker is None
    # Even a nonblocking project question affecting this task prevents archiving.
    Questions(workspace).ask(
        project.id,
        QuestionCreate(
            question="Keep the finding?",
            context="Consider archive",
            recommendation="Keep it",
            affected_task_ids=[task.id],
        ),
    )
    blocked = reads.task(project.id, task.id)
    assert blocked.archive_blocker == "Resolve pending questions before archiving."
    with pytest.raises(ApplicationError) as error:
        workspace.edit_task(
            project.id, task.id, TaskEdit(expected_revision=task.revision, archived=True)
        )
    assert error.value.message == blocked.archive_blocker
    active = workspace.create_task(project.id, task_request(title="Active task"))
    active = workspace.record_progress(
        project.id, active.id, TaskProgress(expected_revision=active.revision, status="in_progress")
    )
    assert "Finish the task or return it to Backlog" in (
        reads.task(project.id, active.id).archive_blocker or ""
    )
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1") as client:
        edited = client.put(
            f"/api/projects/{project.id}/view/tasks/{active.id}",
            json={"expected_revision": active.revision, "title": "Still active"},
        )
        assert edited.status_code == 200
        assert edited.json()["archive_blocker"] == reads.task(project.id, active.id).archive_blocker
