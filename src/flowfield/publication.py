"""Frozen assignment snapshots and concrete execution requirements."""

import sqlite3
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from flowfield.document_models import DocumentSnapshot
from flowfield.task_read_facts import TaskReadFacts

if TYPE_CHECKING:
    from flowfield.application import TaskRevision

Readiness = Literal["ready", "blocked", "needs_reconciliation"]
DEPENDENCY_CHANGED = "Prerequisites changed. Review recorded work with the coordinator."


class Publication(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    agreement_revision: int
    task_revision: int
    author: str
    created_at: str
    completion: Literal["code", "report"] = "code"
    target_branch: str | None = None
    documents: list[DocumentSnapshot] = Field(default_factory=list)


def requires_reconciliation(task: "TaskRevision") -> bool:
    return bool(
        task.reconciliation_reason
        or (
            task.status not in ("backlog", "up_next")
            and task.publication
            and task.publication.agreement_revision != task.agreement_revision
        )
    )


def blocked_dependencies(task: "TaskRevision", completed: set[str]) -> list[str]:
    """Delivered snapshots have no live prerequisite obligations."""
    return (
        [key for key in task.dependencies if key not in completed] if task.status != "done" else []
    )


def readiness(task: "TaskRevision", *, blocked: bool) -> Readiness:
    if requires_reconciliation(task):
        return "needs_reconciliation"
    return "blocked" if blocked else "ready"


def execution_issue(
    db: sqlite3.Connection, task: "TaskRevision", *, facts: TaskReadFacts | None = None
) -> str | None:
    if task.archived:
        return None
    if requires_reconciliation(task):
        return task.reconciliation_reason or "Requirements changed. Reconcile the recorded work."
    if task.status not in ("backlog", "up_next"):
        return None
    has_description = (
        facts.row["has_description"]
        if facts
        else db.execute(
            "SELECT length(trim(json_extract(data, '$.body'), char(9)||char(10)||char(13)||' ')) "
            "FROM tasks WHERE workspace_id=? AND id=?",
            (task.workspace_id, task.id),
        ).fetchone()[0]
    )
    target_branch = None
    if task.completion == "code":
        if facts:
            target_branch = facts.row["target_branch"]
        else:
            row = db.execute(
                "SELECT json_extract(data,'$.target_branch') FROM integration_settings "
                "WHERE project_id=?",
                (task.project_id,),
            ).fetchone()
            target_branch = row[0] if row else None
    return execution_requirement(bool(has_description), task.completion, target_branch)


def execution_requirement(
    has_description: bool, completion: str, target_branch: str | None
) -> str | None:
    if not has_description:
        return "Add a task description before execution."
    if completion == "code" and not target_branch:
        return "Configure the project's delivery target before execution."
    return None
