"""Shared, bounded delivery projection for managed task questions."""

import json
import sqlite3

from pydantic import BaseModel

from flowfield import worker_settings


class InputDelivery(BaseModel):
    state: str
    message: str
    can_edit: bool = False
    can_stop: bool = False
    run_id: str | None = None


def other_input_blocks(
    db: sqlite3.Connection, workspace: str, task: str, question: str, subtask: str | None
) -> bool:
    """Sibling input waits are local; task-wide planning blockers still apply."""
    return (
        db.execute(
            "SELECT 1 FROM questions q WHERE workspace_id=? AND id<>? "
            "AND status IN ('open','answered') AND "
            "json_extract(data,'$.blocking_scope') IS NOT NULL "
            "AND (task_id=? OR EXISTS (SELECT 1 FROM "
            "json_each(data,'$.affected_task_ids') WHERE value=?)) "
            "AND NOT (? IS NOT NULL AND EXISTS (SELECT 1 FROM runs r "
            "WHERE r.id=json_extract(q.data,'$.origin_run_id') AND r.workspace_id=q.workspace_id "
            "AND r.task_id=q.task_id AND r.subtask_id IS NOT NULL AND r.subtask_id<>?)) LIMIT 1",
            (workspace, question, task, task, subtask, subtask),
        ).fetchone()
        is not None
    )


def question_delivery(
    db: sqlite3.Connection, workspace_id: str, identity: str
) -> InputDelivery | None:
    """One bounded, current projection for question, task and attention views."""
    from flowfield.execution_models import Run

    review_question = db.execute(
        "SELECT data FROM questions WHERE workspace_id=? AND id=? "
        "AND json_extract(data,'$.origin_review_id') IS NOT NULL",
        (workspace_id, identity),
    ).fetchone()
    if review_question:
        question = json.loads(review_question[0])
        row_review = db.execute(
            "SELECT data FROM task_reviews WHERE id=?", (question["origin_review_id"],)
        ).fetchone()
        if not row_review:
            return InputDelivery(
                state="Review unavailable", message="Inspect the retained review before continuing."
            )
        review = json.loads(row_review[0])
        status = review["status"]
        editable = (
            status == "waiting"
            and question["status"] in ("open", "answered")
            and not question.get("consumed_answer_revision")
        )
        label = (
            "Withdrawn"
            if question["status"] == "withdrawn"
            else "Needs your answer"
            if question["status"] == "open"
            else "Answer saved"
            if status == "waiting"
            else "Reviewing changes"
            if status in ("queued", "preparing", "running")
            else "Review complete"
            if status == "decided"
            else "Review needs attention"
        )
        return InputDelivery(
            state=label,
            can_edit=editable,
            message=review.get("problem")
            or "Your answer resumes the scoped review when the queue and capacity allow it.",
        )

    row = db.execute(
        "SELECT task_id,status,json_extract(data,'$.origin_run_id') AS origin, "
        "json_extract(data,'$.continuation_run_id') AS continuation FROM questions "
        "WHERE workspace_id=? AND id=?",
        (workspace_id, identity),
    ).fetchone()
    if not row or not row["origin"]:
        return None
    run = Run.model_validate_json(
        db.execute(
            "SELECT json_set(data,'$.result',NULL,'$.feedback','','$.setup_checks',json('[]'),"
            "'$.setup_commands',json('[]'),'$.correction',NULL,'$.next_correction',NULL,"
            "'$.problem',substr(json_extract(data,'$.problem'),1,700)) "
            "FROM runs WHERE workspace_id=? AND (id=? OR "
            "json_extract(data,'$.input_question_id')=?) ORDER BY number DESC LIMIT 1",
            (
                workspace_id,
                row["continuation"] or row["origin"],
                identity if row["continuation"] else None,
            ),
        ).fetchone()[0]
    )
    editable = row["status"] in ("open", "answered") and not row["continuation"]

    def state(name: str, message: str, *, stop: bool = False) -> InputDelivery:
        return InputDelivery(
            state=name, message=message, can_edit=editable, can_stop=stop, run_id=run.id
        )

    if row["status"] == "withdrawn":
        return state("Withdrawn", "This input was withdrawn; it will not launch work.")
    if row["continuation"]:
        labels = {
            "contributed": (
                "Work submitted",
                "This contribution is ready for the task's combined review.",
            ),
            "preparing": ("Resuming", "Preparing work with your saved answer."),
            "running": ("Working", "The worker owns the continuation with your answer."),
            "in_review": (
                "Work submitted",
                "Continued work was submitted. Open the task for validation and result status.",
            ),
            "waiting_for_input": (
                "New question",
                "The worker needs further clarification; see the new question.",
            ),
            "accepted": ("Result approved", "Open the task for the current delivery outcome."),
            "changes_requested": ("Changes requested", "Feedback continues on the same task."),
            "failed": (
                "Continuation blocked",
                run.problem or "Open the task to inspect the failed continuation.",
            ),
            "stopped": (
                "Stopped",
                "Work was stopped. Resume explicitly from the task when appropriate.",
            ),
            "stopping": ("Stopping", "Waiting for the owned process to stop."),
            "uncertain": (
                "Check execution",
                "Confirm the previous process stopped before restarting work.",
            ),
        }
        name, message = labels[run.status]
        return state(name, message, stop=run.status in ("preparing", "running"))
    if run.status == "uncertain":
        return state(
            "Check execution", "Confirm the previous process stopped before restarting work."
        )
    if run.status in ("stopping", "stopped", "failed"):
        return state(
            "Continuation blocked", run.problem or "Work was stopped; reconcile before continuing."
        )
    latest = db.execute(
        "SELECT id FROM runs WHERE workspace_id=? AND task_id=? "
        "AND subtask_id IS ? ORDER BY number DESC LIMIT 1",
        (workspace_id, row["task_id"], run.subtask_id),
    ).fetchone()[0]
    task = json.loads(
        db.execute(
            "SELECT json_set(data,'$.body','','$.change_note','') FROM tasks "
            "WHERE workspace_id=? AND id=?",
            (workspace_id, row["task_id"]),
        ).fetchone()[0]
    )
    target_row = db.execute(
        "SELECT json_extract(data,'$.target_branch') FROM integration_settings WHERE project_id=?",
        (run.project_id,),
    ).fetchone()
    target = target_row[0] if target_row else None
    if (
        latest != run.id
        or task["archived"]
        or task["reconciliation_reason"]
        or task["agreement_revision"] != run.agreement_revision
        or (run.target_branch is not None and target != run.target_branch)
    ):
        return state(
            "Needs update",
            (
                task["reconciliation_reason"]
                or "The task's assignment, ownership or destination changed since this question."
            )
            + " Ask the coordinator whether this answer still applies.",
        )
    if row["status"] == "open":
        return state("Needs your answer", "Send an answer to continue this task.", stop=True)
    if run.status in ("running", "preparing"):
        return state(
            "Saving work",
            "Your answer is saved. Waiting for the previous worker to stop and preserve its work.",
            stop=True,
        )
    if run.status != "waiting_for_input" or not run.input_checkpoint:
        return state(
            "Continuation blocked",
            "The unfinished work could not be prepared. Inspect the task before retrying.",
        )
    if other_input_blocks(db, workspace_id, row["task_id"], identity, run.subtask_id):
        return state(
            "Waiting for input",
            "Your answer is saved. Another blocking question needs resolution.",
            stop=True,
        )
    if db.execute(
        "SELECT 1 FROM tasks WHERE workspace_id=? AND id IN (SELECT value FROM json_each(?)) "
        "AND json_extract(data,'$.status')<>'done'",
        (workspace_id, json.dumps(task["dependencies"])),
    ).fetchone():
        return state(
            "Waiting for prerequisites",
            "Your answer is saved. Complete the task's prerequisites first.",
            stop=True,
        )
    worker = worker_settings.read_workspace(db, workspace_id)
    if not worker.enabled:
        return state(
            "Paused",
            "Your answer is saved. Enable the queue when ready to continue.",
            stop=True,
        )
    if worker.problem:
        return state("Continuation blocked", worker.problem, stop=True)
    occupied = worker_settings.occupancy(db, workspace=workspace_id)
    if occupied.active + occupied.uncertain >= worker.max_parallel:
        return state(
            "Waiting for capacity",
            "Your answer is saved; another attempt owns the available worker slot.",
            stop=True,
        )
    return state("Resuming", "Your answer is saved and awaits an eligible worker slot.", stop=True)
