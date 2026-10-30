"""One human-facing task state derived from canonical execution and input ownership."""

import sqlite3
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, PrivateAttr

from flowfield.browser_links import workspace_href
from flowfield.task_read_facts import TaskReadFacts, read_task_facts

if TYPE_CHECKING:
    from flowfield.application import Workspace


class WorkState(BaseModel):
    _event_id: str | None = PrivateAttr(default=None)

    def for_event(self, identity: str) -> "WorkState":
        self._event_id = identity
        return self

    label: str
    tone: Literal["active", "attention", "waiting", "complete", "idle"] = "idle"
    href: str | None = None
    reason: Literal["prerequisites", "scheduling"] | None = None


def action_label(label: str, *, queue_enabled: bool) -> str:
    """Task summaries describe progress; exact actions retain their recovery detail."""
    return {
        "Preparing reviewer": "Reviewing",
        "Reviewing changes": "Reviewing",
        "Reviewing result": "Reviewing",
        "Reviewing updated guidance": "Reviewing",
        "Guidance review queued": "Queued",
        "Review queued": "Queued",
        "Awaiting review": "Queued",
        "Guidance review paused": "Queue paused",
        "Review paused": "Queue paused",
        "Paused": "Queue paused",
        "Waiting for capacity": "Queued",
        "Waiting for worker reply": "Queued" if queue_enabled else "Queue paused",
        "Waiting for review answer": "Needs your answer",
        "Check reviewer state": "Check review",
        "Retry guidance review": "Review failed",
        "Retry review": "Review failed",
        "Guidance review stopped": "Review stopped",
        "Retaining results": "Finalizing",
    }.get(label, label)


def event_state(
    kind: str, status: str | None, identity: str, current: WorkState, *, decision: str | None = None
) -> WorkState:
    """Live state belongs only to its owning event; historical facts never inherit it."""
    if decision in ("request_changes", "ask_human", "ask_worker"):
        return WorkState(
            label="Changes requested" if decision == "request_changes" else "Question asked",
            tone="waiting",
        )
    target = current._event_id
    if kind == "result" and status == "delivered":
        return WorkState(label="Delivered", tone="complete")
    if kind == "result" and target == identity and current.label == "Reviewing":
        return WorkState(label="Awaiting reviewer", tone="waiting")
    if kind in ("attempt", "result", "question") and target == identity:
        return current.model_copy(update={"href": None})
    if kind == "attempt":
        return WorkState(
            label={
                "accepted": "Finished",
                "contributed": "Contribution completed",
                "in_review": "Work submitted",
                "replied": "Replied",
                "changes_requested": "Changes requested",
                "waiting_for_input": "Asked a question",
                "stopped": "Stopped",
                "failed": "Failed",
            }.get(status or "", "Earlier attempt"),
            tone="complete"
            if status in ("accepted", "in_review", "replied", "contributed")
            else "attention"
            if status in ("failed", "uncertain")
            else "waiting"
            if status in ("changes_requested", "waiting_for_input")
            else "idle",
        )
    if kind == "result":
        return WorkState(
            label="Delivered" if status == "delivered" else "Result submitted", tone="complete"
        )
    if kind == "review":
        return WorkState(
            label={"decided": "Review complete", "running": "Reviewing results"}.get(
                status or "", (status or "Review").capitalize()
            ),
            tone="active"
            if status in ("preparing", "running")
            else "complete"
            if status == "decided"
            else "attention"
            if status in ("failed", "uncertain")
            else "idle"
            if status == "stale"
            else "waiting",
        )
    if kind == "state":
        return WorkState(label="Recorded", tone="idle")
    if kind in ("approval", "delivery"):
        return WorkState(label="Recorded", tone="complete")
    if kind == "reply" and status == "recorded":
        return WorkState(label="Observation recorded", tone="complete")
    if status and (kind in ("activity", "reply") or (kind == "question" and status == "withdrawn")):
        return WorkState(label=status.replace("_", " ")[:1].upper() + status.replace("_", " ")[1:])
    return WorkState(label="Recorded", tone="complete")


def question_state(db: sqlite3.Connection, project: str, identity: str) -> WorkState:
    from flowfield.input_delivery import question_delivery

    row = db.execute(
        "SELECT status,json_extract(data,'$.revision') AS revision "
        "FROM questions WHERE workspace_id=? AND id=?",
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
        href=f"{workspace_href(project)}/requests/question/{identity}",
    ).for_event(f"question:{identity}:{row['revision']}")


def task_state(
    workspace: "Workspace",
    db: sqlite3.Connection,
    project: str,
    task_id: str,
    *,
    facts: TaskReadFacts | None = None,
) -> WorkState:
    from flowfield.result_actions import guidance_action, result_action
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
                "preparing": "Starting work",
                "running": "Working",
                "stopping": "Stopping",
                "uncertain": "Check worker state",
            }[status],
            tone="attention" if status == "uncertain" else "active",
            href=href,
        ).for_event("attempt:" + task["owner_id"])
    if task["question_id"]:
        return question_state(db, project, task["question_id"])
    if (
        task["scheduling_problem"]
        and not task["archived"]
        and task["status"] in ("up_next", "in_progress")
    ):
        return WorkState(label="Cannot start", tone="attention", href=href, reason="scheduling")
    if task["reply_pending"]:
        return WorkState(
            label="Queued" if facts.queue_enabled else "Queue paused",
            tone="waiting",
            href=href,
        )
    from flowfield.guidance_reconciliation import pending_task

    # Changed documents do not schedule a Backlog task or create a paused review.
    pending_guidance = pending_task(workspace, db, project, task_id)
    if pending_guidance and task["status"] != "backlog":
        action = guidance_action(db, project, task_id)
        label = action_label(action.label, queue_enabled=facts.queue_enabled)
        return WorkState(
            label=label,
            tone="active"
            if label == "Reviewing"
            else "attention"
            if label in ("Choose reviewer", "Check review", "Review failed")
            else "waiting",
            href=href,
        )
    if task["reason"]:
        return WorkState(label="Update task", tone="attention", href=href)
    if task["result_id"] and not (pending_guidance and task["status"] == "backlog"):
        # Load exact evidence only when the result owns the visible state.
        result = ResultVersion.model_validate_json(
            db.execute(
                "SELECT data FROM result_versions WHERE id=?", (task["result_id"],)
            ).fetchone()[0]
        )
        action = result_action(workspace, db, result)
        if action.label != "Earlier result":
            label = action_label(action.label, queue_enabled=facts.queue_enabled)
            tone: Literal["active", "attention", "waiting", "complete", "idle"] = "waiting"
            if result.status in ("preparing", "delivering"):
                tone = "active"
            elif label == "Reviewing":
                tone = "active"
            elif label in ("Check review", "Review failed"):
                tone = "attention"
            elif result.status == "delivered":
                tone = "complete"
            elif result.status in ("blocked", "stale"):
                tone = "attention"
            return WorkState(
                label="Done"
                if result.status == "delivered" and action.action == "none"
                else "Integration blocked"
                if action.action == "retry-delivery"
                else label,
                tone=tone,
                href=f"{workspace_href(project)}/results/{result.id}",
            ).for_event("result:" + result.id)
    if task["latest_status"] in ("failed", "stopped") and task["status"] != "up_next":
        return WorkState(
            label="Worker failed" if task["latest_status"] == "failed" else "Work stopped",
            tone="attention" if task["latest_status"] == "failed" else "waiting",
            href=href,
        ).for_event("attempt:" + task["latest_id"])
    if task["archived"]:
        return WorkState(label="Archived")
    if task["status"] == "done":
        return WorkState(label="Done", tone="complete")
    if task["waiting_dependencies"]:
        return WorkState(
            label="Waiting on prerequisites", tone="waiting", href=href, reason="prerequisites"
        )
    if task["status"] == "up_next":
        from flowfield.publication import execution_requirement

        if execution_requirement(
            bool(task["has_description"]), task["completion"], task["target_branch"]
        ):
            return WorkState(label="Needs setup", tone="attention", href=href)
        return WorkState(
            label="Queued" if facts.queue_enabled else "Queue paused", tone="waiting", href=href
        )
    return WorkState(label="Not scheduled" if task["status"] == "backlog" else "Idle", href=href)
