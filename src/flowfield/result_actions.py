"""One current-result action projection for browser, attention and coordinator reads."""

import json
import sqlite3
from typing import TYPE_CHECKING

from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.integration_models import DELIVERY_BLOCKERS
from flowfield.result_models import ResultAction, ResultVersion

if TYPE_CHECKING:
    from flowfield.application import Workspace


def guidance_action(db: sqlite3.Connection, workspace: str, task: str) -> ResultAction:
    from flowfield.reviewer_settings import Reviewers
    from flowfield.worker_settings import read_workspace

    if not Reviewers.resolved(db, workspace, task).effective.agent:
        return ResultAction(
            owner="human",
            action="settings",
            settings="reviewer",
            label="Choose reviewer",
            reason="Choose a reviewer to reconcile updated guidance.",
        )
    row = db.execute(
        "SELECT status FROM task_reviews WHERE workspace_id=? AND task_id=? "
        "AND json_extract(data,'$.guidance') IS NOT NULL ORDER BY number DESC LIMIT 1",
        (workspace, task),
    ).fetchone()
    label = {
        "preparing": "Preparing reviewer",
        "running": "Reviewing updated guidance",
        "waiting": "Waiting for review answer",
        "uncertain": "Check reviewer state",
        "failed": "Retry guidance review",
        "stopped": "Guidance review stopped",
    }.get(
        row[0] if row else "",
        "Guidance review queued"
        if read_workspace(db, workspace).enabled
        else "Guidance review paused",
    )
    return ResultAction(
        owner="reviewer",
        action="wait",
        label=label,
        reason="Open the task for review progress and recovery.",
    )


def result_action(
    workspace: "Workspace", db: sqlite3.Connection, value: ResultVersion
) -> ResultAction:
    if db.execute(
        "SELECT json_extract(data,'$.archived') FROM tasks WHERE workspace_id=? AND id=?",
        (value.workspace_id, value.task_id),
    ).fetchone()[0]:
        return ResultAction(owner="none", action="none", label="Archived task")
    execution = Execution(workspace)
    latest = db.execute(
        "SELECT id,status FROM runs WHERE workspace_id=? AND task_id=? ORDER "
        "BY number DESC LIMIT 1",
        (value.workspace_id, value.task_id),
    ).fetchone()
    selected = db.execute(
        "SELECT id FROM result_versions WHERE workspace_id=? AND task_id=? "
        "ORDER BY version DESC LIMIT 1",
        (value.workspace_id, value.task_id),
    ).fetchone()
    if not latest or latest[0] != value.run_id or selected[0] != value.id:
        return ResultAction(owner="none", action="none", label="Earlier result")
    run = execution._run(db, value.workspace_id, value.run_id)
    from flowfield.guidance_reconciliation import pending_task

    if pending_task(
        workspace,
        db,
        value.workspace_id,
        value.task_id,
        value if value.status not in ("changes_requested", "cancelled", "delivered") else None,
    ):
        return guidance_action(db, value.workspace_id, value.task_id)
    if value.status in ("preparing", "delivering"):
        return ResultAction(
            owner="service",
            action="wait",
            label=("Retaining results" if value.completion == "report" else "Checking changes")
            if value.status == "preparing"
            else "Integrating changes",
        )
    replying = db.execute(
        "SELECT 1 FROM task_replies WHERE workspace_id=? AND task_id=? "
        "AND json_extract(data,'$.status')='pending'",
        (value.workspace_id, value.task_id),
    ).fetchone()
    if replying:
        return ResultAction(owner="worker_queue", action="wait", label="Waiting for worker reply")
    settings = execution._settings(db, value.workspace_id)
    if value.status == "changes_requested":
        task = workspace._task(db, value.workspace_id, value.task_id)
        if task.readiness in ("draft", "needs_reconciliation") and task.preparation_issue:
            return ResultAction(
                owner="coordinator",
                action="coordinator",
                label="Prepare follow-up",
                reason=task.preparation_issue,
            )
        return ResultAction(
            owner="worker_queue",
            action="wait",
            label="Waiting for capacity" if settings.enabled else "Paused",
            reason=(
                "The service will start the requested follow-up when capacity is available."
                if settings.enabled
                else "Changes requested. The worker queue is paused; "
                "enable it to start the follow-up."
            ),
        )
    if value.status == "delivered":
        agreement = db.execute(
            "SELECT json_extract(data,'$.agreement_revision') FROM tasks "
            "WHERE workspace_id=? AND id=?",
            (value.workspace_id, value.task_id),
        ).fetchone()[0]
        if agreement != run.agreement_revision:
            return ResultAction(owner="none", action="none", label="Earlier result")
        return ResultAction(owner="none", action="none", label="Delivered")
    task = workspace._task(db, value.workspace_id, value.task_id)
    revised_assignment = False
    try:
        execution._current_assignment(db, run, task)
    except ApplicationError:
        # Reconciliation authorizes a revised assignment, never the old candidate.
        # A blocked code result can then use the existing correction operation.
        from flowfield.integration import Integrations

        destination = (
            Integrations(workspace)._settings(db, task.project_id).target_branch
            if task.project_id
            else None
        )
        revised_assignment = (
            task.publication_status == "published"
            and task.publication is not None
            and task.publication.completion == "code"
            and destination is not None
            and task.publication.target_branch == destination
            and task.readiness == "ready"
            and value.completion == "code"
            and value.status in ("blocked", "stale")
        )
        if not revised_assignment:
            return ResultAction(
                owner="coordinator",
                action="coordinator",
                label="Update task",
                reason=(
                    "Requirements or prerequisites changed. Resume the "
                    "coordinator with this task link before requesting revised work."
                ),
            )
    if value.status not in ("delivered", "changes_requested"):
        review = db.execute(
            "SELECT status FROM task_reviews WHERE workspace_id=? AND task_id=? "
            "AND (result_id=? OR status IN ('preparing','running','uncertain')) "
            "AND status IN ('queued','preparing','running','waiting','uncertain',"
            "'failed','stopped') AND (status NOT IN ('failed','stopped') OR ?) "
            "ORDER BY number DESC LIMIT 1",
            (
                value.workspace_id,
                value.task_id,
                value.id,
                value.status == "ready" or value.problem_code == "partial_outcome",
            ),
        ).fetchone()
        if review:
            return ResultAction(
                owner="reviewer",
                action="wait",
                label={
                    "queued": "Review queued" if settings.enabled else "Review paused",
                    "preparing": "Preparing reviewer",
                    "running": "Reviewing result",
                    "waiting": "Waiting for review answer",
                    "uncertain": "Check reviewer state",
                    "failed": "Retry review",
                    "stopped": "Review stopped",
                }[review[0]],
                reason="Open the task for review evidence and recovery.",
            )
        if value.status == "ready":
            from flowfield.reviewer_settings import Reviewers
            from flowfield.task_reviews import TaskReview, TaskReviews

            policy = Reviewers.resolved(db, value.workspace_id, value.task_id).effective
            if policy.enabled:
                if not policy.agent:
                    return ResultAction(
                        owner="human",
                        action="settings",
                        settings="reviewer",
                        label="Choose reviewer",
                        reason="Choose a reviewer in task Settings to assess this result.",
                    )
                row = db.execute(
                    "SELECT data FROM task_reviews WHERE result_id=? ORDER BY number DESC LIMIT 1",
                    (value.id,),
                ).fetchone()
                assessment = TaskReview.model_validate_json(row[0]) if row else None
                if (
                    not assessment
                    or assessment.status != "decided"
                    or not assessment.verdict
                    or assessment.verdict.action != "approve"
                    or TaskReviews(workspace).freshness_problem(db, assessment) is not None
                ):
                    return ResultAction(
                        owner="reviewer",
                        action="wait",
                        label="Awaiting review" if settings.enabled else "Review paused",
                    )
        if value.status == "ready":
            return ResultAction(owner="human", action="review_result", label="Review changes")
    if value.approved_at and value.problem_code in DELIVERY_BLOCKERS:
        return ResultAction(
            owner="human",
            action="retry-delivery",
            label="Retry integration",
            reason=value.problem
            or "Resolve the checkout blocker, then retry. Your approval is preserved.",
        )
    if not revised_assignment and value.problem_code in (
        "assignment_changed",
        "delivery_target_changed",
        "delivery_destination_changed",
        "report_changes_code",
    ):
        return ResultAction(
            owner="coordinator",
            action="coordinator",
            label="Update task",
            reason="Ask the coordinator to align the task and destination with this result.",
        )
    if value.problem_code == "partial_outcome":
        return ResultAction(
            owner="human",
            action="request_changes",
            label="Request follow-up",
            reason=(
                "Describe what the worker should finish on this task. "
                "Discuss changes to its scope with the Coordinator."
            ),
        )
    if value.problem_code == "output_unavailable":
        return ResultAction(
            owner="human",
            action="prepare",
            label="Retry file delivery",
            reason="Restore the retained file's original bytes from backup, then retry delivery.",
        )
    if value.problem_code in ("runtime_setup_failed", "runtime_changed_source"):
        current = db.execute(
            "SELECT data FROM integration_settings WHERE project_id=?", (run.project_id,)
        ).fetchone()
        revision = json.loads(current[0])["revision"] if current else None
        if value.settings_revision is not None and revision != value.settings_revision:
            return ResultAction(
                owner="human",
                action="prepare",
                label="Retry checks",
                reason="Setup settings changed. Check this candidate again before approval.",
                settings="integration",
            )
        return ResultAction(
            owner="human",
            action="settings",
            label="Fix project setup",
            reason="Update the failed setup command, then return here to retry checks.",
            settings="integration",
        )
    if revised_assignment or value.problem_code in (
        "integration_conflict",
        "checks_failed",
        "correction_no_progress",
    ):
        if not settings.selection:
            return ResultAction(
                owner="human",
                action="settings",
                label="Choose worker model",
                reason="Choose a worker before requesting a correction.",
                settings="workers",
            )
        return ResultAction(
            owner="human",
            action="correct",
            label="Request correction",
            reason=(
                "The worker will receive the failure evidence and return a new result for approval."
            )
            + (" The queue is paused." if not settings.enabled else ""),
        )
    if value.status == "cancelled" or value.problem_code in (
        "target_changed",
        "settings_changed",
        "validation_stale",
        "target_checked_out",
        "git_failed",
        "local_io_failed",
    ):
        return ResultAction(
            owner="human",
            action="prepare",
            label="Recheck changes",
            reason=(
                "Check these changes against the current destination. Fresh approval is required."
            ),
        )
    return ResultAction(
        owner="coordinator",
        action="coordinator",
        label="Inspect blocker",
        reason=(
            "Resume the coordinator with this task and the recorded failure before restarting work."
        ),
    )


def reuse_action(
    workspace: "Workspace", db: sqlite3.Connection, value: ResultVersion
) -> ResultAction | None:
    """Current reuse readiness is separate from the immutable delivery outcome."""
    if value.completion != "code" or value.status != "delivered":
        return None
    if result_action(workspace, db, value).label != "Delivered":
        return None
    run = Execution(workspace)._run(db, value.workspace_id, value.run_id)
    if run.code_available:
        return ResultAction(owner="none", action="none", label="Validated for reuse")
    return ResultAction(
        owner="human",
        action="revalidate",
        label="Check destination",
        reason=(
            "Delivery succeeded. Current destination validation is needed before dependent "
            "work can use this code. Checking does not change the branch or delivery approval."
        ),
    )
