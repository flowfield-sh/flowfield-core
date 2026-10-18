"""One human-facing task state derived from canonical execution and input ownership."""

import sqlite3
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from flowfield.browser_links import workspace_href
from flowfield.task_read_facts import TaskReadFacts, read_task_facts

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
        "FROM questions q LEFT JOIN tasks t ON t.workspace_id=q.workspace_id AND t.id=q.task_id "
        "WHERE q.workspace_id=? AND q.id=?",
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
            f"{workspace_href(project)}/tasks/{row['key']}/conversation/question:{identity}"
            + (f":{row['revision']}" if row["status"] in ("open", "answered", "withdrawn") else "")
        )
        if row["key"]
        else f"{workspace_href(project)}/inbox/{identity}",
    )


def task_state(
    workspace: "Workspace",
    db: sqlite3.Connection,
    project: str,
    task_id: str,
    *,
    facts: TaskReadFacts | None = None,
) -> WorkState:
    from flowfield.result_actions import result_action
    from flowfield.result_models import ResultVersion

    facts = facts or next(iter(read_task_facts(workspace, db, project, task_id).values()))
    task = facts.row
    task_id = task["id"]
    href = f"{workspace_href(project)}/tasks/{task['key']}"
    if task["owner_id"]:
        status = task["owner_status"]
        if status == "running" and task["permission_pending"]:
            return WorkState(label="Review tool permission", tone="waiting", href=href)
        return WorkState(
            label={
                "preparing": "Preparing worker",
                "running": "Working",
                "stopping": "Stopping worker",
                "uncertain": "Check worker state",
            }[status],
            tone="attention" if status == "uncertain" else "active",
            href=href + "/conversation/attempt:" + task["owner_id"],
        )
    if task["question_id"]:
        return question_state(db, project, task["question_id"])
    if task["reason"]:
        return WorkState(label="Update task", tone="attention", href=href)
    if task["scheduling_problem"] and not task["archived"]:
        return WorkState(label=task["scheduling_problem"], tone="attention", href=href)
    if task["reply_pending"]:
        return WorkState(
            label="Reply queued" if facts.queue_enabled else "Reply paused",
            tone="waiting",
            href=href,
        )
    if task["result_id"]:
        # Load exact evidence only when the result owns the visible state.
        result = ResultVersion.model_validate_json(
            db.execute(
                "SELECT data FROM result_versions WHERE id=?", (task["result_id"],)
            ).fetchone()[0]
        )
        action = result_action(workspace, db, result)
        if action.label != "Earlier result":
            tone: Literal["active", "attention", "waiting", "complete", "idle"] = "waiting"
            if result.status in ("preparing", "delivering"):
                tone = "active"
            elif action.label in ("Reviewing changes", "Reviewing result", "Preparing reviewer"):
                tone = "active"
            elif action.label == "Check reviewer state":
                tone = "attention"
            elif result.status == "delivered":
                tone = "complete" if action.action == "none" else "attention"
            elif result.status in ("blocked", "stale"):
                tone = "attention"
            return WorkState(
                label="Integration blocked" if action.action == "retry-delivery" else action.label,
                tone=tone,
                href=href + "/conversation/result:" + result.id,
            )
    if task["latest_status"] in ("failed", "stopped") and task["status"] != "up_next":
        return WorkState(
            label="Worker failed" if task["latest_status"] == "failed" else "Work stopped",
            tone="attention" if task["latest_status"] == "failed" else "waiting",
            href=href + "/conversation/attempt:" + task["latest_id"],
        )
    if task["archived"]:
        return WorkState(label="Archived")
    if task["status"] == "done":
        return WorkState(label="Done", tone="complete")
    if task["waiting_dependencies"]:
        return WorkState(
            label="Waiting on prerequisites", tone="waiting", href=href, reason="prerequisites"
        )
    if task["status"] == "up_next":
        return WorkState(
            label="Queued" if facts.queue_enabled else "Queue paused", tone="waiting", href=href
        )
    return WorkState(label="Not scheduled" if task["status"] == "backlog" else "Idle", href=href)
