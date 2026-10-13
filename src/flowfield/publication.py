"""Publication records an informed check of an assignment, never permission to run."""

import sqlite3
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from flowfield.document_models import DocumentSnapshot
from flowfield.milestone_models import MilestoneBinding

if TYPE_CHECKING:
    from flowfield.application import TaskRevision

PublicationStatus = Literal["draft", "published", "needs_reconciliation"]
Readiness = Literal["ready", "blocked", "draft", "needs_reconciliation"]


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


def stages_need_reconciliation(
    db: sqlite3.Connection, project_id: str, task_id: str, agreement_revision: int
) -> bool:
    row = db.execute(
        "SELECT json_extract(data,'$.agreement_revision'), json_array_length(data,'$.stages') "
        "FROM stage_plans WHERE project_id=? AND task_id=? ORDER BY revision DESC LIMIT 1",
        (project_id, task_id),
    ).fetchone()
    return bool(row and row[1] and row[0] != agreement_revision)


def readiness(
    task: "TaskRevision",
    publication: PublicationStatus,
    *,
    blocked: bool,
    stages_stale: bool = False,
) -> Readiness:
    if task.reconciliation_reason:
        return "needs_reconciliation"
    if publication == "needs_reconciliation" and task.status not in ("backlog", "up_next"):
        return "needs_reconciliation"
    if stages_stale and not task.archived and task.status != "done":
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
) -> str | None:
    """Human next action without exposing the snapshot protocol or implying agent wake-up."""
    # Browser labels own the generic draft hint; concrete blockers stay visible everywhere.
    if task.archived:
        return None
    if task.reconciliation_reason:
        return task.reconciliation_reason
    if issue := verification_issue(db, task):
        return issue
    if task.status != "done" and stages_need_reconciliation(
        db, task.project_id, task.id, task.agreement_revision
    ):
        return "Task changed. Ask the coordinator to update its stages."
    state = publication_status(task)
    if state == "needs_reconciliation":
        return "Task changed. Review it with the coordinator before continuing."
    if state == "published" or task.status not in ("backlog", "up_next"):
        return None
    has_description = db.execute(
        "SELECT length(trim(json_extract(data, '$.body'), char(9)||char(10)||char(13)||' ')) "
        "FROM tasks WHERE project_id=? AND id=?",
        (task.project_id, task.id),
    ).fetchone()[0]
    if not has_description:
        return "Describe the requested outcome with your coordinator."
    if task.publication:
        return "Task changed. Ask the coordinator to prepare it again."
    return "Ask the coordinator to prepare this task." if include_draft_hint else None


def verification_issue(db: sqlite3.Connection, task: "TaskRevision") -> str | None:
    binding = task.publication.verification_id if task.publication else None
    if not binding or task.status == "done" or task.archived:
        return None
    row = db.execute("SELECT status FROM milestone_verifications WHERE id=?", (binding,)).fetchone()
    if not row or row[0] != "verifying":
        return "Ask the coordinator to prepare a new milestone verification."
    return None
