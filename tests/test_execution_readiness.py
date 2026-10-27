"""Invalid role settings block reservation, without launching or losing work."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from execution_fixtures import BASE, fixture
from pydantic import ValidationError

from flowfield.agent_models import AgentChoice
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, SettingsEdit
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate


def test_workspace_creation_requires_all_roles():
    with pytest.raises(ValidationError):
        WorkspaceCreate(name="Missing role settings")


def test_missing_reviewer_blocks_before_attempt_and_defaults_follow_changes(tmp_path):
    execution = fixture(tmp_path)
    reviews = Reviewers(execution.workspace)
    reviews.configure("harbor", ReviewerEdit(expected_revision=2, enabled=True))
    with pytest.raises(ApplicationError, match="reviewer settings"):
        execution.claim("harbor", BASE, {BASE: set()})
    assert execution.page("harbor").items == []
    reviews.configure(
        "harbor",
        ReviewerEdit(expected_revision=3, enabled=True, selection=AgentChoice(model="review")),
    )
    settings = execution.settings("harbor")
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=settings.revision, selection=AgentChoice(model="new-worker")
        ),
    )
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run and run.agent_settings.choice.model == "new-worker"
    settings = execution.settings("harbor")
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=settings.revision, selection=AgentChoice(model="next-worker")
        ),
    )
    assert execution.get("harbor", run.id).agent_settings.choice.model == "new-worker"


def test_unavailable_agent_and_pause_block_scheduler_without_failed_attempt(tmp_path, monkeypatch):
    execution = fixture(tmp_path)
    service = Supervisor(execution.workspace)
    monkeypatch.setattr(service, "_process_delivery", lambda _: None)
    monkeypatch.setattr(service.review_execution, "schedule", AsyncMock())
    unavailable = AsyncMock(
        side_effect=ApplicationError("unavailable", "Harness disconnected", 409)
    )
    monkeypatch.setattr(service, "validate_agent_choice", unavailable)

    async def exercise():
        await service._schedule_once()
        await asyncio.gather(*service.delivery_jobs.values())
        assert execution.page("harbor").items == []
        with execution.workspace.connection() as db:
            reason = db.execute(
                "SELECT scheduling_problem FROM tasks WHERE id='task-0'"
            ).fetchone()[0]
        assert "Coordinator unavailable" in reason and "disconnected" in reason
        execution.queue(
            "harbor",
            QueueEdit(expected_revision=execution.settings("harbor").revision, enabled=False),
        )
        unavailable.reset_mock()
        await service._schedule_once()
        await asyncio.gather(*service.delivery_jobs.values())
        unavailable.assert_not_called()
        assert execution.page("harbor").items == []

    asyncio.run(exercise())


def test_settings_race_requires_fresh_preflight(tmp_path, monkeypatch):
    execution = fixture(tmp_path)
    service = Supervisor(execution.workspace)
    monkeypatch.setattr(service, "validate_agent_choice", AsyncMock())
    checked = asyncio.run(service.check_task_agents("harbor", "task-0"))
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=execution.settings("harbor").revision,
            selection=AgentChoice(model="changed"),
        ),
    )
    assert execution.claim("harbor", BASE, {BASE: set()}, checked_agents=checked) is None
    assert execution.page("harbor").items == []


def test_scheduling_diagnostics_are_retained_once_and_survive_recovery(tmp_path):
    from flowfield.browser import BrowserReads

    execution = fixture(tmp_path)
    reason = "Local contributions conflict. Ask the Coordinator to reconcile shared files."
    details = "Git conflict evidence\nREADME.md: conflicting contribution edits"
    for _ in range(3):
        execution.task_problem("harbor", "task-0", reason, details=details)
    reads = BrowserReads(execution.workspace)
    card = reads.workspace_board("harbor").tasks[0]
    assert card.state.label == "Cannot start" and card.state.tone == "attention"
    assert reason not in card.model_dump_json()
    assert reads.task("harbor", "task-0").scheduling_problem == reason
    with execution.workspace.connection() as db:
        assert (
            db.execute("SELECT scheduling_problem FROM tasks WHERE id='task-0'").fetchone()[0]
            == reason
        )
        assert (
            db.execute("SELECT count(*) FROM activity WHERE body=?", (details,)).fetchone()[0] == 1
        )
    execution.task_problem("harbor", "task-0", None)
    assert reads.task("harbor", "task-0").scheduling_problem is None
    with execution.workspace.connection() as db:
        assert (
            db.execute("SELECT scheduling_problem FROM tasks WHERE id='task-0'").fetchone()[0]
            is None
        )
        assert (
            db.execute("SELECT count(*) FROM activity WHERE body=?", (details,)).fetchone()[0] == 1
        )
