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


def question_delivery(
    db: sqlite3.Connection, project_id: str, identity: str
) -> InputDelivery | None:
    """One bounded, current projection for question, task and attention views."""
    from flowfield.execution_models import Run

    row = db.execute(
        "SELECT task_id,status,json_extract(data,'$.origin_run_id') AS origin, "
        "json_extract(data,'$.continuation_run_id') AS continuation FROM questions "
        "WHERE project_id=? AND id=?",
        (project_id, identity),
    ).fetchone()
    if not row or not row["origin"]:
        return None
    run = Run.model_validate_json(
        db.execute(
            "SELECT json_set(data,'$.result',NULL,'$.feedback','','$.setup_checks',json('[]'),"
            "'$.setup_commands',json('[]'),'$.correction',NULL,'$.next_correction',NULL,"
            "'$.problem',substr(json_extract(data,'$.problem'),1,700)) "
            "FROM runs WHERE project_id=? AND (id=? OR "
            "json_extract(data,'$.input_question_id')=?) ORDER BY number DESC LIMIT 1",
            (
                project_id,
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
        "SELECT id FROM work_runs WHERE project_id=? AND task_id=? ORDER BY number DESC LIMIT 1",
        (project_id, row["task_id"]),
    ).fetchone()[0]
    task = json.loads(
        db.execute(
            "SELECT json_set(data,'$.body','','$.change_note','') FROM tasks "
            "WHERE project_id=? AND id=?",
            (project_id, row["task_id"]),
        ).fetchone()[0]
    )
    settings_row = db.execute(
        "SELECT data FROM integration_settings WHERE project_id=?", (project_id,)
    ).fetchone()
    target = json.loads(settings_row[0])["target_branch"] if settings_row else None
    if (
        latest != run.id
        or task["archived"]
        or task["reconciliation_reason"]
        or task["agreement_revision"] != run.agreement_revision
        or (run.completion == "code" and target != run.target_branch)
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
    if db.execute(
        "SELECT 1 FROM questions WHERE project_id=? AND id<>? "
        "AND status IN ('open','answered') AND json_extract(data,'$.blocking_scope') IS NOT NULL "
        "AND (task_id=? OR EXISTS (SELECT 1 FROM json_each(data,'$.affected_task_ids') "
        "WHERE value=?))",
        (project_id, identity, row["task_id"], row["task_id"]),
    ).fetchone():
        return state(
            "Waiting for input",
            "Your answer is saved. Another blocking question needs resolution.",
            stop=True,
        )
    if db.execute(
        "SELECT 1 FROM tasks WHERE project_id=? AND id IN (SELECT value FROM json_each(?)) "
        "AND json_extract(data,'$.status')<>'done'",
        (project_id, json.dumps(task["dependencies"])),
    ).fetchone():
        return state(
            "Waiting for prerequisites",
            "Your answer is saved. Complete the task's prerequisites first.",
            stop=True,
        )
    worker = worker_settings.read_project(db, project_id)
    if not worker.enabled:
        return state(
            "Paused",
            "Your answer is saved. Enable the queue when ready to continue.",
            stop=True,
        )
    if worker.problem:
        return state("Continuation blocked", worker.problem, stop=True)
    occupied = worker_settings.occupancy(db, project=project_id)
    if occupied.active + occupied.uncertain >= worker.max_parallel:
        return state(
            "Waiting for capacity",
            "Your answer is saved; another attempt owns the available worker slot.",
            stop=True,
        )
    return state("Resuming", "Your answer is saved and awaits an eligible worker slot.", stop=True)
