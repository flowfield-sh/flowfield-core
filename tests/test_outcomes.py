"""Outcome declarations and shared remedies preserve approval and task scope."""

import os

import pytest
from project_fixtures import reconcile_fixture_stages
from pydantic import ValidationError
from test_results import approve, current, fixture

from flowfield.adapters.local_execution import LocalHost
from flowfield.application import TaskEdit
from flowfield.attention import attention_counts, attention_page
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, WorkerResult, WorkerSubmission
from flowfield.integration_models import IntegrationConfig
from flowfield.result_brief import result_brief
from flowfield.result_models import ResultReview
from flowfield.worker_tools import worker_tools


def test_model_must_explicitly_account_for_the_agreed_outcome():
    schema = next(t["inputSchema"] for t in worker_tools() if t["name"] == "submit_result")
    assert "outcome" in schema["required"]
    for fields in (
        {},
        {"outcome": "partial"},
        {"outcome": "complete", "remaining_work": "UI unfinished"},
    ):
        with pytest.raises(ValidationError):
            WorkerSubmission(summary="One piece works", checks="Unit checks", **fields)


@pytest.mark.parametrize("completion", ["code", "report"])
def test_partial_work_cannot_complete_or_approve_but_continues_on_same_task(tmp_path, completion):
    service, repo, old_run = fixture(
        tmp_path,
        completion=completion,
        changed=completion == "code",
        outcome="partial",
        remaining_work="Finish the second agreed behavior",
    )
    service.results.process("harbor")
    partial = current(service)
    assert partial.status == "blocked" and partial.problem_code == "partial_outcome"
    assert partial.next_action.action == "request_changes"
    assert service.workspace.task("harbor", "work").status == "in_review"
    assert service.integrations.head("harbor") == old_run.base_commit
    with pytest.raises(ApplicationError, match="Finish the agreed outcome"):
        approve(service, partial)
    service.results.review(
        "harbor",
        partial.id,
        ResultReview(
            expected_revision=partial.revision,
            candidate_commit=partial.candidate_commit,
            action="request_changes",
            note=partial.report.remaining_work,
        ),
    )
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    head = service.integrations.head("harbor")
    run = service.execution.claim("harbor", head, service._available("harbor", repo, head))
    assert run and run.task_id == old_run.task_id and run.predecessor_id == old_run.id
    assert run.feedback == partial.report.remaining_work
    service.execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(service.workspace.directory, repo, run.id, run.base_commit)
    if completion == "code":
        (env.checkout / "result.txt").write_text("Both requested behaviors implemented")
    commit, _ = env.snapshot(run.base_commit)
    service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        commit=commit,
        result=WorkerResult(summary="Entire agreed outcome", checks="Verified both behaviors"),
    )
    service.results.process("harbor")
    complete = current(service)
    assert complete.id != partial.id and complete.approved_at is None
    if completion == "code":
        assert complete.status == "ready"
        approve(service, complete)
        service.results.process("harbor")
    assert current(service).status == "delivered"
    assert service.workspace.task("harbor", "work").status == "done"
    assert service.results.get("harbor", partial.id).report.outcome == "partial"


def test_report_delivery_is_not_endorsement_and_cannot_race_changed_scope(tmp_path, monkeypatch):
    service, _, _ = fixture(tmp_path / "complete", completion="report", changed=False)
    service.results.process("harbor")
    value = current(service)
    assert value.status == "delivered" and value.completed_at and value.approved_at is None
    assert value.approved_by is None
    with service.workspace.connection() as db:
        assert attention_counts(db, "harbor").get("action", 0) == 0
    racing, _, _ = fixture(tmp_path / "racing", completion="report", changed=False)
    from flowfield import results

    actual = results.git
    changed = False

    def change_scope(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            task = racing.workspace.task("harbor", "work")
            racing.workspace.edit_task(
                "harbor",
                "work",
                TaskEdit(expected_revision=task.revision, body="A broader required investigation"),
            )
        return actual(*args, **kwargs)

    monkeypatch.setattr(results, "git", change_scope)
    racing.results.process("harbor")
    assert current(racing).problem_code == "assignment_changed"
    assert racing.workspace.task("harbor", "work").status != "done"


def test_recovery_agrees_across_result_attention_and_coordinator(tmp_path):
    service, _, _ = fixture(tmp_path, checks=["exit 7"])
    service.results.process("harbor")
    value = current(service)
    assert value.next_action.action == "correct"
    assert value.next_action.settings is None
    item = attention_page(service.workspace, "harbor", "action").items[0]
    assert item.next_action == value.next_action.label
    with service.workspace.connection() as db:
        brief = result_brief(service.workspace, db, "harbor")["items"][0]
    assert brief["next_action"] == value.next_action.action
    assert brief["action_reason"] == value.next_action.reason


def test_failed_setup_offers_settings_then_retry_after_settings_change(tmp_path):
    service, _, _ = fixture(tmp_path)
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch=settings.target_branch,
            checks=settings.checks,
            setup_commands=["exit 3"],
        ),
    )
    service.results.process("harbor")
    value = current(service)
    assert value.next_action.settings == "integration" and value.next_action.action == "settings"
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch=settings.target_branch,
            checks=settings.checks,
            setup_commands=[],
        ),
    )
    assert current(service).next_action.action == "prepare"


def test_reconciled_blocked_scope_offers_correction_without_approving_old_code(tmp_path):
    from flowfield.application import TaskReconcile
    from flowfield.execution_models import RunAction

    service, _, _ = fixture(tmp_path, checks=["exit 7"])
    service.results.process("harbor")
    task = service.workspace.task("harbor", "work")
    task = service.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, body="Deliver the revised agreed outcome"),
    )
    assert current(service).next_action.action == "coordinator"
    service.workspace.reconcile_task(
        "harbor",
        task.id,
        TaskReconcile(
            expected_revision=task.revision,
            completion="code",
            note="Human clarified the remaining outcome",
        ),
    )
    reconcile_fixture_stages(service.workspace, "harbor", task.id)
    value = current(service)
    assert value.next_action.action == "correct"
    assert value.approved_at is None
    revised = service.results.correct(
        "harbor",
        value.id,
        RunAction(expected_revision=value.revision, note="Finish the revised assignment"),
    )
    assert revised.status == "changes_requested" and revised.approved_at is None
    assert service.workspace.task("harbor", "work").status == "up_next"


def test_report_reconciliation_does_not_offer_a_code_correction(tmp_path):
    from flowfield.application import TaskReconcile

    service, _, _ = fixture(tmp_path, checks=["exit 7"])
    service.results.process("harbor")
    task = service.workspace.task("harbor", "work")
    service.workspace.reconcile_task(
        "harbor",
        task.id,
        TaskReconcile(
            expected_revision=task.revision,
            completion="report",
            note="Scope needs findings rather than this code change",
        ),
    )
    assert current(service).next_action.action == "coordinator"


@pytest.mark.parametrize("completion", ["code", "report"])
def test_idle_correction_edits_scope_and_stages_before_reconciliation(tmp_path, completion):
    from flowfield.application import TaskReconcile
    from flowfield.stage_models import Stage, StageChange
    from flowfield.stages import Stages

    service, repo, old_run = fixture(
        tmp_path,
        completion=completion,
        changed=completion == "code",
        outcome="partial",
        remaining_work="Runtime access is missing",
    )
    service.results.process("harbor")
    original = current(service)
    task = service.workspace.task("harbor", "work")
    before = Stages(service.workspace).get("harbor", task.id)
    edited = service.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(
            expected_revision=task.revision,
            body="Verify the outcome with a one-time native permission request if needed.",
            stages=StageChange(
                expected_revision=before.revision,
                stages=[Stage(id="verify", title="Verify", outcome="Execute with agreed access")],
                reason="Human clarified the runtime access boundary",
            ),
        ),
    )
    plan = Stages(service.workspace).get("harbor", task.id)
    assert edited.reconciliation_reason
    assert plan.agreement_revision == edited.agreement_revision
    assert plan.stages[0].outcome == "Execute with agreed access"
    assert service.execution.claim("harbor", old_run.base_commit, {}) is None
    retained = service.results.get("harbor", original.id)
    assert retained.report == original.report and retained.approved_at is None
    service.workspace.reconcile_task(
        "harbor",
        task.id,
        TaskReconcile(
            expected_revision=edited.revision, completion=completion, note="Scope reviewed"
        ),
    )
    assert Stages(service.workspace).get("harbor", task.id) == plan
    assert service.integrations.head("harbor") == old_run.base_commit
