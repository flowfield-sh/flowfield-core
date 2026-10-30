"""Saved task requirements run without a separate preparation gate."""

from pathlib import Path

import pytest
from project_fixtures import adopt, task_request

from flowfield.agent_models import AgentChoice
from flowfield.application import (
    ProjectSetup,
    TaskEdit,
    TaskPriority,
    Workspace,
)
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit
from flowfield.reads import ContextReads


def setup(tmp_path: Path):
    w = Workspace(tmp_path / "state")
    adopt(w, ProjectSetup(path=str(tmp_path / "project")))
    return w


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
            completion="report",
        ),
    )
    assert len(snapshots) == 1 and snapshots[0][0].readiness == "ready"
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
            completion="report",
        ),
    )
    assert task.status == "up_next" and task.position == position
    assert task.publication.agreement_revision == task.agreement_revision
    assert task.readiness == "ready" and task.execution_issue is None
    assert execution.claim("project", "a" * 40, {}) is None
    execution.queue("project", QueueEdit(expected_revision=settings.revision, enabled=True))
    run = execution.claim("project", "a" * 40, {"a" * 40: set()})
    assert run and run.task_id == task.id
    with pytest.raises(ApplicationError, match="working on this task"):
        w.edit_task(
            "project",
            task.id,
            TaskEdit(
                expected_revision=w.task("project", task.id).revision,
                body="Unrelated work",
                completion="report",
            ),
        )
    assert w.task("project", task.id).body == task.body
    assert execution.claim("project", "a" * 40, {}) is None


def test_saved_requirements_keep_dependency_gates_and_show_next_owner(tmp_path: Path):
    w = setup(tmp_path)
    first = w.create_task(
        "project", task_request(title="Findings", body="Report findings", completion="report")
    )
    second = w.create_task(
        "project",
        task_request(
            title="Recommendation",
            body="Use the findings",
            dependencies=[first.key],
            completion="report",
        ),
    )
    assert second.readiness == "blocked"
    assert second.blocked_by[0].id == first.id
    changed = w.edit_task(
        "project", second.id, TaskEdit(expected_revision=second.revision, body="Use new findings")
    )
    assert changed.readiness == "blocked"
    assert changed.execution_issue is None
    assert changed.publication.agreement_revision == changed.agreement_revision
    browser = BrowserReads(w).workspace_board("project")
    assert (
        next(t for t in browser.tasks if t.id == second.id).execution_issue
        == changed.execution_issue
    )
    assert ContextReads(w).task("project", second.key)["execution_issue"] == changed.execution_issue
    empty = w.create_task("project", task_request(title="Unfinished idea"))
    assert "Add a task description" in BrowserReads(w).task("project", empty.id).execution_issue


def test_setup_added_after_task_creation_allows_execution_without_another_edit(tmp_path):
    from flowfield.adapters.git_workspace import git
    from flowfield.integration import Integrations
    from flowfield.integration_models import IntegrationConfig

    w = setup(tmp_path)
    repo = tmp_path / "project"
    git(repo, "init", "-b", "main")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Initial",
    )
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    task = w.create_task(
        "project",
        task_request(
            project_id="project", title="Fix the bug", body="Fix and test it", status="up_next"
        ),
    )
    assert task.readiness == "blocked" and task.publication.target_branch is None
    assert BrowserReads(w).task("project", task.id).state.label == "Needs setup"
    execution = Execution(w)
    settings = execution.settings("project")
    execution.queue("project", QueueEdit(expected_revision=settings.revision, enabled=True))
    assert execution.claim("project", base, {base: set()}) is None
    Integrations(w).configure(
        "project", IntegrationConfig(expected_revision=1, target_branch="main")
    )
    run = execution.claim("project", base, {base: set()})
    assert run and run.completion == "code" and run.target_branch == "main"
    updated = w.task("project", task.id)
    assert updated.publication.task_revision == task.revision + 1
    assert updated.publication.agreement_revision == run.agreement_revision
