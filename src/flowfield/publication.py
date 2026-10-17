"""Publication records an informed check of an assignment, never permission to run."""

import sqlite3
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from flowfield.document_models import DocumentSnapshot
from flowfield.milestone_models import MilestoneBinding
from flowfield.task_read_facts import TaskReadFacts

if TYPE_CHECKING:
    from flowfield.application import TaskRevision

PublicationStatus = Literal["draft", "published", "needs_reconciliation"]
Readiness = Literal["ready", "blocked", "draft", "needs_reconciliation"]
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
    milestone: MilestoneBinding | None = None
    verification_id: str | None = None


def publication_status(task: "TaskRevision") -> PublicationStatus:
    check = task.publication
    if check is None:
        return "draft"
    if check.agreement_revision != task.agreement_revision:
        return "draft" if task.status in ("backlog", "up_next") else "needs_reconciliation"
    return "published"


def readiness(
    task: "TaskRevision",
    publication: PublicationStatus,
    *,
    blocked: bool,
) -> Readiness:
    if task.reconciliation_reason:
        return "needs_reconciliation"
    if publication == "needs_reconciliation" and task.status not in ("backlog", "up_next"):
        return "needs_reconciliation"
    if blocked:
        return "blocked"
    if task.status in ("backlog", "up_next") and publication != "published":
        return "draft"
    return "ready"


def preparation_issue(
    db: sqlite3.Connection,
    task: "TaskRevision",
    *,
    include_draft_hint: bool = True,
    facts: TaskReadFacts | None = None,
) -> str | None:
    """Human next action without exposing the snapshot protocol or implying agent wake-up."""
    # Browser labels own the generic draft hint; concrete blockers stay visible everywhere.
    if task.archived:
        return None
    if task.reconciliation_reason:
        return task.reconciliation_reason
    if issue := verification_issue(db, task, facts=facts):
        return issue
    state = publication_status(task)
    if state == "needs_reconciliation":
        return "Task changed. Review it with the coordinator before continuing."
    if state == "published" or task.status not in ("backlog", "up_next"):
        return None
    has_description = (
        facts.row["has_description"]
        if facts is not None
        else db.execute(
            "SELECT length(trim(json_extract(data, '$.body'), char(9)||char(10)||char(13)||' ')) "
            "FROM tasks WHERE project_id=? AND id=?",
            (task.project_id, task.id),
        ).fetchone()[0]
    )
    if not has_description:
        return "Describe the requested outcome with your coordinator."
    if task.publication:
        return "Task changed. Ask the coordinator to prepare it again."
    return "Ask the coordinator to prepare this task." if include_draft_hint else None


def verification_issue(
    db: sqlite3.Connection, task: "TaskRevision", *, facts: TaskReadFacts | None = None
) -> str | None:
    binding = task.publication.verification_id if task.publication else None
    if not binding or task.status == "done" or task.archived:
        return None
    if facts is not None:
        status = facts.row["verification_status"]
    else:
        row = db.execute(
            "SELECT status FROM milestone_verifications WHERE id=?", (binding,)
        ).fetchone()
        status = row[0] if row else None
    if status != "verifying":
        return "Ask the coordinator to prepare a new milestone verification."
    return None
