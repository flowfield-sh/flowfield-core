"""Intent and preparation commit together without scheduling or weakening freshness."""

from pathlib import Path

import pytest
from project_fixtures import adopt, fixture_stage_change, task_request

from flowfield.agent_models import AgentChoice
from flowfield.application import (
    ProjectSetup,
    TaskEdit,
    TaskPreparation,
    TaskPriority,
    Workspace,
)
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit
from flowfield.questions import QuestionCreate, Questions
from flowfield.reads import ContextReads


def setup(tmp_path: Path):
    w = Workspace(tmp_path / "state")
    adopt(w, ProjectSetup(path=str(tmp_path / "project")))
    return w


def preparation(completion="report"):
    return TaskPreparation(completion=completion)


def test_capture_refinement_and_queue_are_separate(tmp_path: Path):
    w = setup(tmp_path)
    execution = Execution(w)
    settings = execution.configure(
        "project",
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="fake", effort="low")),
    )
    snapshots = []
    w.on_change = lambda _: snapshots.append(w.tasks("project"))
    task = w.create_task(
        "project",
        task_request(
            title="Find the cause",
            body="Report evidence and recommendation.",
            preparation=preparation(),
        ),
    )
    assert len(snapshots) == 1 and snapshots[0][0].publication_status == "published"
    assert task.status == "backlog" and not execution.settings("project").enabled
    task = w.prioritize_task(
        "project", task.id, TaskPriority(expected_revision=task.revision, status="up_next")
    )
    position = task.position
    task = w.edit_task(
        "project",
        task.id,
        TaskEdit(
            expected_revision=task.revision,
            body="Report evidence, uncertainty and recommendation.",
            stages=fixture_stage_change(w, "project", task.id),
            preparation=preparation(),
        ),
    )
    assert task.status == "up_next" and task.position == position
    assert task.publication.agreement_revision == task.agreement_revision
    assert task.readiness == "ready" and task.preparation_issue is None
    assert execution.claim("project", "a" * 40, {}) is None
    execution.queue("project", QueueEdit(expected_revision=settings.revision, enabled=True))
    run = execution.claim("project", "a" * 40, {"a" * 40: set()})
    assert run and run.task_id == task.id
    with pytest.raises(ApplicationError, match="upcoming"):
        w.edit_task(
            "project",
            task.id,
            TaskEdit(
                expected_revision=w.task("project", task.id).revision,
                body="Unrelated work",
                preparation=preparation(),
            ),
        )
    assert w.task("project", task.id).body == task.body
    assert execution.claim("project", "a" * 40, {}) is None


def test_failed_preparation_rolls_back_creation_edits_history_and_notifications(tmp_path: Path):
    w = setup(tmp_path)
    notifications = []
    w.on_change = notifications.append
    with pytest.raises(ApplicationError, match="delivery target"):
        w.create_task(
            "project",
            task_request(
                title="Code",
                body="Implement the change.",
                preparation=preparation(completion="code"),
            ),
        )
    assert not w.tasks("project") and not notifications
    task = w.create_task(
        "project",
        task_request(title="Report", body="Original agreement", preparation=preparation()),
    )
    assert task.key.endswith("-1")
    w.edit_task(
        "project", task.id, TaskEdit(expected_revision=task.revision, body="Use recorded evidence.")
    )
    before = w.task("project", task.id)
    notifications.clear()
    with pytest.raises(ApplicationError, match="stale"):
        w.edit_task(
            "project",
            task.id,
            TaskEdit(
                expected_revision=task.revision,
                body="Overwrite original",
                preparation=preparation(),
            ),
        )
    assert w.task("project", task.id) == before and not notifications
    with pytest.raises(ApplicationError):
        w.edit_task(
            "project",
            task.id,
            TaskEdit(expected_revision=1, body="Stale overwrite", preparation=preparation()),
        )
    q = Questions(w).ask(
        "project",
        QuestionCreate(
            task_id=task.id,
            question="Which audience?",
            context="Missing audience",
            recommendation="Internal team",
            blocking_scope="Audience",
        ),
    )
    before = w.task("project", task.id)
    with pytest.raises(ApplicationError, match="blocking questions"):
        w.edit_task(
            "project",
            task.id,
            TaskEdit(
                expected_revision=before.revision,
                body="Wrong audience",
                preparation=preparation(),
            ),
        )
    assert w.task("project", task.id) == before
    assert q.id == before.blocking_questions[0].id


def test_preparation_keeps_dependency_gates_and_manual_edits_show_next_owner(tmp_path: Path):
    w = setup(tmp_path)
    first = w.create_task(
        "project", task_request(title="Findings", body="Report findings", preparation=preparation())
    )
    second = w.create_task(
        "project",
        task_request(
            title="Recommendation",
            body="Use the findings",
            dependencies=[first.key],
            preparation=preparation(),
        ),
    )
    assert second.publication_status == "published" and second.readiness == "blocked"
    assert second.blocked_by[0].id == first.id
    changed = w.edit_task(
        "project", second.id, TaskEdit(expected_revision=second.revision, body="Use new findings")
    )
    assert changed.publication_status == "draft"
    assert "stages" in changed.preparation_issue.lower()
    browser = BrowserReads(w).board("project")
    assert (
        next(t for t in browser.tasks if t.id == second.id).preparation_issue
        == changed.preparation_issue
    )
    assert (
        ContextReads(w).task("project", second.key)["preparation_issue"]
        == changed.preparation_issue
    )
    empty = w.create_task("project", task_request(title="Unfinished idea"))
    assert (
        "Describe the requested outcome"
        in BrowserReads(w).task("project", empty.id).preparation_issue
    )
