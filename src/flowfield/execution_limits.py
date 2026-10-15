"""Durable task allowances; waiting never starts a fresh allowance."""

import json
import sqlite3
import time
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from flowfield.errors import ApplicationError
from flowfield.execution_models import Record

if TYPE_CHECKING:
    from flowfield.application import Workspace


class LimitPolicy(Record):
    revision: int = 1
    active_seconds: int = Field(default=1800, ge=1, le=86400)
    corrections: int = Field(default=2, ge=0, le=20)


class PolicyEdit(Record):
    expected_revision: int = Field(ge=1)
    active_seconds: int = Field(ge=1, le=86400)
    corrections: int = Field(ge=0, le=20)


class TaskLimits(Record):
    project_id: str
    task_id: str
    revision: int = 1
    active_seconds_allowed: int = 1800
    active_seconds_used: float = 0
    corrections_allowed: int = 2
    corrections_used: int = 0
    blocked: Literal["active_time", "corrections"] | None = None
    earlier_usage_unknown: bool = False
    updated_at: str


class LimitExtension(Record):
    expected_revision: int = Field(ge=1)
    active_seconds: int = Field(default=0, ge=0, le=86400)
    corrections: int = Field(default=0, ge=0, le=20)
    note: str = Field(min_length=1, max_length=2000)


def policy(db: sqlite3.Connection, workspace: str) -> LimitPolicy:
    row = db.execute(
        "SELECT data FROM execution_policies WHERE workspace_id=?", (workspace,)
    ).fetchone()
    return LimitPolicy.model_validate_json(row[0]) if row else LimitPolicy()


def read(db: sqlite3.Connection, project: str, task: str) -> TaskLimits | None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_execution_limits'").fetchone():
        return None
    row = db.execute(
        "SELECT data FROM task_execution_limits WHERE project_id=? AND task_id=?", (project, task)
    ).fetchone()
    return TaskLimits.model_validate_json(row[0]) if row else None


def save(db: sqlite3.Connection, value: TaskLimits) -> None:
    from flowfield.application import now

    value.revision += 1
    value.updated_at = now()
    db.execute(
        "INSERT INTO task_execution_limits VALUES (?,?,?,?) ON CONFLICT(project_id,task_id) "
        "DO UPDATE SET blocked=excluded.blocked,data=excluded.data",
        (value.project_id, value.task_id, value.blocked, value.model_dump_json()),
    )


def ensure(db: sqlite3.Connection, project: str, task: str) -> TaskLimits:
    from flowfield.application import now
    from flowfield.worker_settings import workspace_id

    value = read(db, project, task)
    if value:
        return value
    defaults = policy(db, workspace_id(db, project) or project)
    # Historical work is retained, but elapsed native work cannot be reconstructed
    # from wall time that includes human waits. Existing retry usage is countable.
    rows = db.execute(
        "SELECT data FROM runs WHERE project_id=? AND task_id=? ORDER BY number", (project, task)
    ).fetchall()
    previous = {json.loads(row[0])["id"]: json.loads(row[0]) for row in rows}
    retries = sum(
        not (
            run.get("input_question_id")
            and previous.get(run.get("predecessor_id"), {}).get("question_id")
            == run["input_question_id"]
        )
        for run in (json.loads(row[0]) for row in rows[1:])
    )
    value = TaskLimits(
        project_id=project,
        task_id=task,
        active_seconds_allowed=defaults.active_seconds,
        corrections_allowed=defaults.corrections,
        corrections_used=retries,
        earlier_usage_unknown=bool(rows),
        updated_at=now(),
    )
    save(db, value)
    return value


def claim(db: sqlite3.Connection, project: str, task: str, *, followup: bool) -> bool:
    value = ensure(db, project, task)
    if value.blocked:
        return False
    if value.active_seconds_used >= value.active_seconds_allowed:
        value.blocked = "active_time"
    elif followup and value.corrections_used >= value.corrections_allowed:
        value.blocked = "corrections"
    elif followup:
        value.corrections_used += 1
    save(db, value)
    return not value.blocked


class ExecutionLimits:
    def __init__(self, store: "Workspace"):
        self.store = store

    def get(self, project: str, task: str) -> TaskLimits | None:
        with self.store.connection() as db:
            identity = self.store._task(db, project, task).id
            return read(db, project, identity)

    def defaults(self, workspace: str) -> LimitPolicy:
        from flowfield.workspaces import Workspaces

        with self.store.connection() as db:
            Workspaces.read(db, workspace)
            return policy(db, workspace)

    def configure(self, workspace: str, request: PolicyEdit) -> LimitPolicy:
        from flowfield.workspaces import Workspaces

        with self.store.connection(write=True) as db:
            Workspaces.read(db, workspace)
            current = policy(db, workspace)
            self.store._current(current.revision, request.expected_revision)
            current = LimitPolicy(
                revision=current.revision + 1,
                active_seconds=request.active_seconds,
                corrections=request.corrections,
            )
            db.execute(
                "INSERT INTO execution_policies VALUES (?,?) ON CONFLICT(workspace_id)"
                " DO UPDATE SET data=excluded.data",
                (workspace, current.model_dump_json()),
            )
            return current

    def clock(
        self, project: str, run_id: str, *, active: bool | None, at: float | None = None
    ) -> bool:
        from flowfield.execution import Execution

        at = time.time() if at is None else at
        with self.store.connection(write=True, project_id=project, notify=False) as db:
            execution = Execution(self.store)
            run = execution._run(db, project, run_id)
            value = ensure(db, project, run.task_id)
            if run.active_since is None and (active is not True or run.status != "running"):
                return not value.blocked
            counting = run.active_since is not None if active is None else active
            if run.active_since is not None:
                elapsed = max(0.0, at - run.active_since)
                run.active_seconds += elapsed
                value.active_seconds_used += elapsed
            if counting and value.active_seconds_used >= value.active_seconds_allowed:
                value.blocked = "active_time"
            run.active_since = (
                at if counting and not value.blocked and run.status == "running" else None
            )
            # Accounting is observational: do not invalidate Stop/review revisions or
            # broadcast/recompute the entire workspace twice a second. Terminal transitions
            # publish the exhausted allowance through the normal state owner.
            db.execute("UPDATE runs SET data=? WHERE id=?", (run.model_dump_json(), run.id))
            save(db, value)
            return not value.blocked

    def extend(self, project: str, task: str, request: LimitExtension) -> TaskLimits:
        with self.store.connection(write=True, project_id=project) as db:
            current_task = self.store._task(db, project, task)
            value = read(db, project, current_task.id)
            if not value or not value.blocked:
                raise ApplicationError(
                    "limit_not_reached", "This task has no exhausted allowance to extend.", 409
                )
            self.store._current(value.revision, request.expected_revision)
            if current_task.archived or current_task.status == "done":
                raise ApplicationError(
                    "inactive_task", "Review the task with the Coordinator first.", 409
                )
            if db.execute(
                "SELECT 1 FROM runs WHERE project_id=? AND task_id=? AND status IN "
                "('preparing','running','stopping','uncertain')",
                (project, current_task.id),
            ).fetchone():
                raise ApplicationError(
                    "worker_active",
                    "Confirm the worker and its commands stopped before extending its allowance.",
                    409,
                )
            from flowfield.execution import Execution

            execution = Execution(self.store)
            row = db.execute(
                "SELECT id FROM work_runs WHERE project_id=? AND task_id=? "
                "ORDER BY number DESC LIMIT 1",
                (project, current_task.id),
            ).fetchone()
            run = execution._run(db, project, row[0]) if row else None
            pending_answer = bool(
                run
                and run.question_id
                and run.input_checkpoint
                and run.agreement_revision == current_task.agreement_revision
                and db.execute(
                    "SELECT 1 FROM questions WHERE id=? AND status IN ('open','answered') "
                    "AND json_extract(data,'$.continuation_run_id') IS NULL",
                    (run.question_id,),
                ).fetchone()
            )
            value.active_seconds_allowed += request.active_seconds
            value.corrections_allowed += request.corrections
            if value.active_seconds_used >= value.active_seconds_allowed or (
                not pending_answer and value.corrections_used >= value.corrections_allowed
            ):
                raise ApplicationError(
                    "insufficient_allowance",
                    "Add enough active time and at least one available follow-up attempt.",
                    409,
                )
            value.blocked = None
            save(db, value)
            # A deliberate continuation preserves its existing agreement and queue choice.
            # Reconciliation remains a gate and cannot be cleared by extending a budget.
            from flowfield.application import TaskRevision, now

            values = current_task.model_dump()
            if current_task.readiness == "ready" and not current_task.reconciliation_reason:
                values["status"] = "up_next"
            values.update(
                revision=current_task.revision + 1,
                updated_at=now(),
                updated_by="human",
                change_note=(
                    f"Added {request.active_seconds} seconds and "
                    f"{request.corrections} follow-up attempts. " + request.note
                ),
            )
            self.store._apply_task(db, TaskRevision(**values))
            if run:
                if pending_answer:
                    run.status = "waiting_for_input"
                run.feedback = (
                    run.feedback + "\n\nContinuation instruction: " + request.note
                ).strip()
                execution._save(db, run)
            return value
