"""One human-facing task state derived from canonical execution and input ownership."""

import sqlite3
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from flowfield.application import Workspace


class WorkState(BaseModel):
    label: str
    tone: Literal["active", "attention", "waiting", "complete", "idle"] = "idle"
    href: str | None = None
    reason: Literal["prerequisites"] | None = None


def event_state(kind: str, status: str | None, identity: str, current: WorkState) -> WorkState:
    """Live state belongs only to its owning event; historical facts never inherit it."""
    target = (current.href or "").split("/conversation/")[-1]
    if kind in ("attempt", "result", "question") and target == identity:
        return current.model_copy(update={"href": None})
    if kind == "attempt":
        return WorkState(
            label={
                "accepted": "Finished",
                "in_review": "Work submitted",
                "replied": "Replied",
                "changes_requested": "Changes requested",
                "waiting_for_input": "Asked a question",
                "stopped": "Stopped",
                "failed": "Failed",
            }.get(status or "", "Earlier attempt"),
            tone="complete" if status in ("accepted", "in_review", "replied") else "idle",
        )
    if kind == "result":
        return WorkState(
            label="Earlier result", tone="complete" if status == "delivered" else "idle"
        )
    if kind in ("approval", "delivery"):
        return WorkState(label="Recorded", tone="complete")
    if status and (kind in ("activity", "reply") or (kind == "question" and status == "withdrawn")):
        return WorkState(label=status.replace("_", " ")[:1].upper() + status.replace("_", " ")[1:])
    return WorkState(label="Recorded", tone="complete")


def question_state(db: sqlite3.Connection, project: str, identity: str) -> WorkState:
    from flowfield.input_delivery import question_delivery

    row = db.execute(
        "SELECT q.status,json_extract(q.data,'$.revision') AS revision,t.key "
        "FROM questions q LEFT JOIN tasks t ON t.project_id=q.project_id AND t.id=q.task_id "
        "WHERE q.project_id=? AND q.id=?",
        (project, identity),
    ).fetchone()
    delivery = question_delivery(db, project, identity)
    return WorkState(
        label="Needs your answer"
        if row["status"] == "open"
        else delivery.state
        if delivery
        else "Resume coordinator",
        tone="waiting",
        href=(
            f"/projects/{project}/tasks/{row['key']}/conversation/question:{identity}"
            + (f":{row['revision']}" if row["status"] in ("open", "answered", "withdrawn") else "")
        )
        if row["key"]
        else f"/projects/{project}/inbox/{identity}",
    )


def task_state(
    workspace: "Workspace", db: sqlite3.Connection, project: str, task_id: str
) -> WorkState:
    from flowfield.execution import Execution
    from flowfield.publication import stages_need_reconciliation
    from flowfield.result_actions import result_action
    from flowfield.result_models import ResultVersion

    task = db.execute(
        "SELECT id,key,json_extract(data,'$.status') AS status, "
        "json_extract(data,'$.archived') AS archived, "
        "json_extract(data,'$.agreement_revision') AS agreement_revision, "
        "json_extract(data,'$.reconciliation_reason') AS reason "
        "FROM tasks WHERE project_id=? AND (id=? OR key=? COLLATE NOCASE) ORDER BY id=? DESC",
        (project, task_id, task_id, task_id),
    ).fetchone()
    task_id = task["id"]
    href = f"/projects/{project}/tasks/{task['key']}"
    owner = db.execute(
        "SELECT id,status,data FROM runs WHERE project_id=? AND task_id=? "
        "AND status IN ('preparing','running','stopping','uncertain') ORDER BY number DESC LIMIT 1",
        (project, task_id),
    ).fetchone()
    if owner:
        status = owner["status"]
        if (
            status == "running"
            and workspace.schema_version >= 32
            and db.execute(
                "SELECT 1 FROM agent_permissions WHERE project_id=? AND task_id=? "
                "AND status='pending' LIMIT 1",
                (project, task_id),
            ).fetchone()
        ):
            return WorkState(label="Review tool permission", tone="waiting", href=href)
        return WorkState(
            label={
                "preparing": "Preparing worker",
                "running": "Working",
                "stopping": "Stopping worker",
                "uncertain": "Check worker state",
            }[status],
            tone="attention" if status == "uncertain" else "active",
            href=href + "/conversation/attempt:" + owner["id"],
        )
    question = db.execute(
        "SELECT id,data,status FROM questions WHERE project_id=? AND task_id=? "
        "AND status IN ('open','answered') ORDER BY number LIMIT 1",
        (project, task_id),
    ).fetchone()
    if question:
        return question_state(db, project, question["id"])
    if task["reason"]:
        return WorkState(label="Update task", tone="attention", href=href)
    if (
        not task["archived"]
        and task["status"] != "done"
        and stages_need_reconciliation(db, project, task_id, task["agreement_revision"])
    ):
        return WorkState(label="Reconcile stages", tone="attention", href=href)
    pending = db.execute(
        "SELECT 1 FROM task_replies WHERE project_id=? AND task_id=? AND "
        "json_extract(data,'$.status')='pending'",
        (project, task_id),
    ).fetchone()
    settings = Execution(workspace)._settings(db, project)
    if pending:
        return WorkState(
            label="Reply queued" if settings.enabled else "Reply paused", tone="waiting", href=href
        )
    version = db.execute(
        "SELECT data FROM result_versions WHERE project_id=? AND task_id=? "
        "ORDER BY version DESC LIMIT 1",
        (project, task_id),
    ).fetchone()
    if version:
        result = ResultVersion.model_validate_json(version[0])
        action = result_action(workspace, db, result)
        if action.label != "Earlier result":
            tone: Literal["active", "attention", "waiting", "complete", "idle"] = "waiting"
            if result.status in ("preparing", "delivering"):
                tone = "active"
            elif result.status == "delivered":
                tone = "complete" if action.action == "none" else "attention"
            elif result.status in ("blocked", "stale"):
                tone = "attention"
            return WorkState(
                label="Integration blocked" if action.action == "retry-delivery" else action.label,
                tone=tone,
                href=href + "/conversation/result:" + result.id,
            )
    latest = db.execute(
        "SELECT id,status FROM runs WHERE project_id=? AND task_id=? ORDER BY number DESC LIMIT 1",
        (project, task_id),
    ).fetchone()
    if latest and latest["status"] in ("failed", "stopped") and task["status"] != "up_next":
        return WorkState(
            label="Worker failed" if latest["status"] == "failed" else "Work stopped",
            tone="attention" if latest["status"] == "failed" else "waiting",
            href=href + "/conversation/attempt:" + latest["id"],
        )
    if task["archived"]:
        return WorkState(label="Archived")
    if task["status"] == "done":
        return WorkState(label="Done", tone="complete")
    if db.execute(
        "SELECT 1 FROM tasks t,json_each(t.data,'$.dependencies') p "
        "JOIN tasks d ON d.project_id=t.project_id AND d.id=p.value "
        "WHERE t.project_id=? AND t.id=? AND json_extract(d.data,'$.status')!='done'",
        (project, task_id),
    ).fetchone():
        return WorkState(
            label="Waiting on prerequisites", tone="waiting", href=href, reason="prerequisites"
        )
    if task["status"] == "up_next":
        return WorkState(
            label="Queued" if settings.enabled else "Queue paused", tone="waiting", href=href
        )
    return WorkState(label="Not scheduled" if task["status"] == "backlog" else "Idle", href=href)
