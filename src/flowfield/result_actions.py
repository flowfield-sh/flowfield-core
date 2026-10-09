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


def result_action(
    workspace: "Workspace", db: sqlite3.Connection, value: ResultVersion
) -> ResultAction:
    execution = Execution(workspace)
    latest = db.execute(
        "SELECT id,status FROM work_runs WHERE project_id=? AND task_id=? ORDER "
        "BY number DESC LIMIT 1",
        (value.project_id, value.task_id),
    ).fetchone()
    selected = db.execute(
        "SELECT id FROM result_versions WHERE project_id=? AND task_id=? "
        "ORDER BY version DESC LIMIT 1",
        (value.project_id, value.task_id),
    ).fetchone()
    if not latest or latest[0] != value.run_id or selected[0] != value.id:
        return ResultAction(owner="none", action="none", label="Earlier result")
    run = execution._run(db, value.project_id, value.run_id)
    if value.status in ("preparing", "delivering"):
        return ResultAction(
            owner="service",
            action="wait",
            label="Checking changes" if value.status == "preparing" else "Integrating changes",
        )
    replying = db.execute(
        "SELECT 1 FROM task_replies WHERE project_id=? AND task_id=? "
        "AND json_extract(data,'$.status')='pending'",
        (value.project_id, value.task_id),
    ).fetchone()
    if replying:
        return ResultAction(owner="worker_queue", action="wait", label="Waiting for worker reply")
    settings = execution._settings(db, value.project_id)
    if value.status == "changes_requested":
        task = workspace._task(db, value.project_id, value.task_id)
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
        if value.completion == "code" and not run.code_available:
            return ResultAction(
                owner="human",
                action="revalidate",
                label="Check destination",
                reason=(
                    "Previously integrated code needs current checks before dependent "
                    "work can use it. This does not change the branch."
                ),
            )
        return ResultAction(owner="none", action="none", label="Completed")
    task = workspace._task(db, value.project_id, value.task_id)
    revised_assignment = False
    try:
        execution._current_assignment(run, task)
    except ApplicationError:
        # Reconciliation authorizes a revised assignment, never the old candidate.
        # A blocked code result can then use the existing correction operation.
        destination = db.execute(
            "SELECT json_extract(data, '$.target_branch') FROM integration_settings "
            "WHERE project_id=?",
            (value.project_id,),
        ).fetchone()
        revised_assignment = (
            task.publication_status == "published"
            and task.publication is not None
            and task.publication.completion == "code"
            and destination is not None
            and task.publication.target_branch == destination[0]
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
            label="Continue work",
            reason=(
                "The agreed outcome is unfinished. Continue on this task, or revise "
                "its scope with the coordinator."
            ),
        )
    if value.problem_code in ("runtime_setup_failed", "runtime_changed_source"):
        current = db.execute(
            "SELECT data FROM integration_settings WHERE project_id=?", (value.project_id,)
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
    if revised_assignment or value.problem_code in ("integration_conflict", "checks_failed"):
        if execution._correction_count(db, value.project_id, value.task_id) >= 2:
            return ResultAction(
                owner="coordinator",
                action="coordinator",
                label="Reconcile next step",
                reason=(
                    "The correction limit was reached. Review the remaining outcome with "
                    "the coordinator."
                ),
            )
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
