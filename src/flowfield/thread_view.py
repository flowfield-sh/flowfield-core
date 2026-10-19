"""Browser conversation presentation, derived from the canonical conversation sources."""

import json
import sqlite3
from typing import Literal

from flowfield.actors import Actor, actor
from flowfield.conversation import COLUMNS, PROJECTION, Conversation, ConversationItem
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.work_state import WorkState, event_state, task_state


class ThreadMessage(Record):
    id: str
    kind: str
    source_id: str
    revision: int | None = None
    created_at: str
    author: str
    actor: Actor
    state: WorkState
    title: str
    question: str | None = None
    body: str
    truncated: bool = False
    status: str | None = None
    result_id: str | None = None
    run_id: str | None = None
    purpose: Literal["work", "assembly"] | None = None
    question_id: str | None = None
    choices: list[str] = []


class ThreadPage(Record):
    items: list[ThreadMessage]
    next_cursor: str | None = None


class ThreadView(Conversation):
    def message(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        task_id: str,
        item: ConversationItem,
        full: bool = False,
        current_state: WorkState | None = None,
    ) -> ThreadMessage:
        row = db.execute(
            PROJECTION + "SELECT data FROM conversation WHERE id=:id",
            {"project": workspace_id, "task": task_id, "id": item.id},
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Conversation item not found.", 404)
        data = json.loads(row[0])
        kind: str = item.kind
        body, title = "", ""
        internal_plan = False
        if kind == "definition":
            before = db.execute(
                "SELECT data FROM task_revisions WHERE workspace_id=? AND task_id=? AND revision=?",
                (workspace_id, task_id, (item.revision or 1) - 1),
            ).fetchone()
            previous = json.loads(before[0]) if before else None
            if previous and data["agreement_revision"] == previous["agreement_revision"]:
                kind = "state"
                title = "Moved to " + data["status"].replace("_", " ").capitalize()
                if data.get("reconciliation_reason"):
                    title = "Scope needs reconciliation"
                if data.get("archived"):
                    title = "Task archived"
                if previous.get("reconciliation_reason") and not data.get("reconciliation_reason"):
                    title = "Task reconciled"
                    body = data["change_note"]
            else:
                title = "Task defined" if not previous else "Task definition updated"
                body = "## " + data["title"] + "\n\n" + data["body"]
                internal_plan = previous is not None and all(
                    data.get(field) == previous.get(field)
                    for field in (
                        "title",
                        "body",
                        "acceptance_criteria",
                        "project_id",
                        "dependencies",
                        "documents",
                        "input_files",
                        "publication",
                    )
                )
                if internal_plan:
                    title = "Work plan updated"
        elif kind == "question":
            title = "Question"
            body = "\n\n".join(
                part
                for part in (
                    data["context"],
                    "Recommendation: " + data["recommendation"] if data["recommendation"] else "",
                )
                if part
            )
            if data["status"] != "withdrawn":
                data["status"] = None
        elif kind == "answer":
            title = "Answer"
            body = data["answer"] or ""
        elif kind == "activity":
            title = "Independent review" if item.author == "reviewer" else data["kind"].capitalize()
            if item.author == "reviewer":
                decision = db.execute(
                    "SELECT json_extract(data,'$.decision.action') FROM review_attempts WHERE id=?",
                    (item.source_id,),
                ).fetchone()
                if decision:
                    title = {
                        "approve": "Reviewer signed off",
                        "request_changes": "Reviewer requested changes",
                        "ask_human": "Reviewer asked a question",
                        "ask_worker": "Reviewer asked the worker",
                    }.get(decision[0], title)
            body = data["body"]
            if data["kind"] == "handoff":
                task = self.workspace._task(db, workspace_id, task_id)
                data["status"] = (
                    "Superseded"
                    if data.get("superseded_by")
                    else "Task changed · Recheck before continuing"
                    if data.get("task_revision") != task.revision
                    else "Selected handoff"
                )
        elif kind == "reply":
            title = {
                "changes": "Feedback",
                "observation": "Testing observation",
            }.get(data["action"], "Message")
            body = data["body"]
            if data["action"] == "observation":
                version = db.execute(
                    "SELECT version FROM result_versions WHERE workspace_id=? AND id=?",
                    (workspace_id, data["binding"]["result_id"]),
                ).fetchone()
                title += f" · Result {version[0]}"
        elif kind == "attempt":
            title = (
                "Contribution work"
                if data.get("subtask_id")
                else "Task assembled"
                if data.get("purpose") == "assembly"
                else "Work started"
            )
            body = data.get("problem") or ""
        elif kind == "result":
            title = "Result " + str(data["version"])
            body = data["report"]["summary"]
        elif kind == "review":
            title = "Reviewing results"
            body = data.get("problem") or ""
        elif kind == "approval":
            title = "Approved for integration"
            body = data.get("approval_note", "")
        elif kind == "delivery":
            title = (
                "Integrated into " + str(data["target_branch"])
                if data["completion"] == "code"
                else "Findings delivered"
            )
        return ThreadMessage(
            id=item.id,
            kind=kind,
            source_id=item.source_id,
            revision=item.revision,
            created_at=item.created_at,
            author=item.author,
            actor=actor(item.author),
            state=event_state(
                kind,
                data.get("status"),
                item.id,
                current_state or task_state(self.workspace, db, workspace_id, task_id),
            ),
            title=title,
            question=data["question"] if kind == "question" else None,
            body=body if full else body[:6000],
            truncated=not full and len(body) > 6000,
            status=data.get("status"),
            result_id=item.source_id
            if kind in ("result", "approval", "delivery")
            else data["binding"]["result_id"]
            if kind == "reply" and data["action"] == "observation"
            else None,
            run_id=item.source_id if kind == "attempt" else data.get("run_id"),
            purpose=data.get("purpose", "work") if kind == "attempt" else None,
            question_id=data.get("question_id") if kind == "activity" else None,
            choices=data.get("choices", []) if kind == "question" else [],
        )

    def page_view(self, workspace_id: str, task_id: str, cursor: str | None = None) -> ThreadPage:
        with self.workspace.connection() as db:
            page = self._page(db, workspace_id, task_id, cursor)
            args = self._args(db, workspace_id, task_id)
            state = task_state(self.workspace, db, workspace_id, args["task"])
            return ThreadPage(
                items=[
                    self.message(db, workspace_id, args["task"], i, current_state=state)
                    for i in page.items
                ],
                next_cursor=page.next_cursor,
            )

    def item_view(
        self, workspace_id: str, task_id: str, item_id: str, full: bool = False
    ) -> ThreadMessage:
        with self.workspace.connection() as db:
            args = self._args(db, workspace_id, task_id)
            row = db.execute(
                PROJECTION + f"SELECT {COLUMNS} FROM conversation WHERE id=:id",
                {**args, "id": item_id},
            ).fetchone()
            if not row and item_id.startswith("question:"):
                # Current-question links resolve to the latest visible exchange, not
                # an internal reservation/application revision omitted from the feed.
                row = db.execute(
                    PROJECTION + f"SELECT {COLUMNS} FROM conversation "
                    "WHERE kind IN ('question','answer') AND source_id=:question "
                    "ORDER BY revision DESC LIMIT 1",
                    {**args, "question": item_id.removeprefix("question:")},
                ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Conversation item not found.", 404)
            return self.message(
                db, workspace_id, args["task"], ConversationItem.model_validate(dict(row)), full
            )
