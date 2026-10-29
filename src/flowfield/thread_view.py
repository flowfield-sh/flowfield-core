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
    order_key: str
    author: str
    actor: Actor
    state: WorkState
    title: str
    question: str | None = None
    body: str
    truncated: bool = False
    has_activity: bool = False
    status: str | None = None
    result_id: str | None = None
    run_id: str | None = None
    purpose: Literal["work", "assembly"] | None = None
    question_id: str | None = None
    choices: list[str] = []
    cycle_start: Literal["correction", "follow_up"] | None = None


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
        review_decision = None
        cycle_start: Literal["correction", "follow_up"] | None = None
        if kind == "definition":
            before = db.execute(
                "SELECT data FROM task_revisions WHERE workspace_id=? AND task_id=? AND revision=?",
                (workspace_id, task_id, (item.revision or 1) - 1),
            ).fetchone()
            previous = json.loads(before[0]) if before else None
            if previous and previous["status"] == "done" and data["status"] != "done":
                cycle_start = "follow_up"
            authored_fields = (
                "title",
                "body",
                "acceptance_criteria",
                "project_id",
                "dependencies",
                "documents",
                "input_files",
                "publication",
                "labels",
            )
            same_definition = previous is not None and all(
                data.get(field) == previous.get(field) for field in authored_fields
            )
            if (
                previous
                and data["agreement_revision"] == previous["agreement_revision"]
                and same_definition
            ):
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
                if same_definition and previous is not None:
                    kind = "subtasks"
                    title = "Subtasks updated"
                    body = subtask_changes(
                        db, workspace_id, task_id, previous["updated_at"], data["updated_at"]
                    )
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
                    review_decision = decision[0]
                    if decision[0] == "request_changes":
                        cycle_start = "correction"
                    title = {
                        "approve": "Reviewer signed off",
                        "adopt": "Guidance reconciled",
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
            if data["action"] == "changes":
                cycle_start = "correction"
            title = {
                "changes": "Feedback",
                "observation": "Review observation",
            }.get(data["action"], "Message")
            body = data["body"]
            if data["action"] in ("changes", "observation") and data["binding"].get("result_id"):
                version = db.execute(
                    "SELECT version FROM result_versions WHERE workspace_id=? AND id=?",
                    (workspace_id, data["binding"]["result_id"]),
                ).fetchone()
                if version:
                    title = (
                        f"Result #{version[0]} feedback"
                        if data["action"] == "changes"
                        else f"Reviewing result #{version[0]} observation"
                    )
        elif kind == "attempt":
            if data.get("correction"):
                cycle_start = "correction"
            elif data.get("predecessor_id"):
                predecessor = db.execute(
                    "SELECT json_extract(data,'$.status') FROM runs WHERE id=?",
                    (data["predecessor_id"],),
                ).fetchone()
                if predecessor and predecessor[0] == "changes_requested":
                    cycle_start = "correction"
            title = (
                "Contribution work"
                if data.get("subtask_id")
                else "Task assembled"
                if data.get("purpose") == "assembly"
                else "Work started"
                if data.get("started_at")
                else "Worker reserved"
            )
            if data.get("subtask_id"):
                child = db.execute(
                    "SELECT data FROM subtask_revisions WHERE workspace_id=? AND task_id=? "
                    "AND subtask_id=? AND revision=?",
                    (workspace_id, task_id, data["subtask_id"], data.get("subtask_revision")),
                ).fetchone()
                if child:
                    subtask = json.loads(child[0])
                    title += f": {subtask['key']} · {subtask['title']}"
                else:
                    title += ": " + data["subtask_id"]
            body = data.get("problem") or ""
        elif kind == "result":
            title = "Result #" + str(data["version"])
            body = data["report"]["summary"]
        elif kind == "review":
            version = db.execute(
                "SELECT version FROM result_versions WHERE workspace_id=? AND id=?",
                (workspace_id, data["result_id"]),
            ).fetchone()
            title = (
                "Reviewing updated guidance"
                if data.get("guidance")
                else f"Reviewing result #{version[0]}"
            )
            body = data.get("problem") or ""
            review_decision = (data.get("verdict") or {}).get("action")
        elif kind == "approval":
            title = "Approved for integration"
            body = data.get("approval_note", "")
        elif kind == "delivery":
            title = (
                "Integrated into " + str(data["target_branch"])
                if data["completion"] == "code"
                else "Findings delivered"
            )
        assembled = kind == "attempt" and data.get("purpose") == "assembly"
        has_activity = False
        if kind in ("attempt", "review") and not assembled:
            has_activity = data.get("status") in ("preparing", "running", "stopping")
            if kind == "attempt":
                evidence = db.execute(
                    "SELECT 1 FROM run_activity WHERE run_id=? AND "
                    "(json_array_length(data,'$.items')>0 OR json_extract(data,'$.omitted')=1 "
                    "OR json_type(data,'$.context')='object')",
                    (item.source_id,),
                ).fetchone()
                has_activity = (
                    has_activity
                    or bool(evidence)
                    or (data.get("usage") or {}).get("total_tokens") is not None
                )
            else:
                evidence = db.execute(
                    "SELECT 1 FROM review_attempts WHERE review_id=? AND "
                    "(json_array_length(data,'$.activity.items')>0 "
                    "OR json_extract(data,'$.activity.omitted')=1 "
                    "OR json_type(data,'$.activity.context')='object' "
                    "OR json_extract(data,'$.usage.total_tokens') IS NOT NULL "
                    "OR coalesce(json_extract(data,'$.problem'),'')!='' "
                    "OR json_extract(data,'$.status')='uncertain') LIMIT 1",
                    (item.source_id,),
                ).fetchone()
                has_activity = has_activity or bool(evidence)
        return ThreadMessage(
            id=item.id,
            kind=kind,
            source_id=item.source_id,
            revision=item.revision,
            created_at=item.created_at,
            order_key=item.order_key,
            author=item.author,
            actor=actor(item.author),
            state=WorkState(label="Contributions assembled", tone="complete")
            if assembled and not body
            else event_state(
                kind,
                data.get("status"),
                item.id,
                current_state or task_state(self.workspace, db, workspace_id, task_id),
                decision=review_decision,
            ),
            title=title,
            question=data["question"] if kind == "question" else None,
            body=body if full else body[:6000],
            truncated=not full and len(body) > 6000,
            has_activity=has_activity,
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
            cycle_start=cycle_start,
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


def subtask_changes(
    db: sqlite3.Connection, workspace: str, task: str, before: str, after: str
) -> str:
    """Describe authored changes in the exact parent revision's time window."""
    entries = []
    for row in db.execute(
        "SELECT data FROM subtask_revisions WHERE workspace_id=? AND task_id=? "
        "AND json_extract(data,'$.updated_at')>? AND json_extract(data,'$.updated_at')<=? "
        "ORDER BY json_extract(data,'$.updated_at'),revision",
        (workspace, task, before, after),
    ):
        value = json.loads(row[0])
        previous = db.execute(
            "SELECT data FROM subtask_revisions WHERE workspace_id=? AND task_id=? "
            "AND subtask_id=? AND revision=?",
            (workspace, task, value["id"], value["revision"] - 1),
        ).fetchone()
        old = json.loads(previous[0]) if previous else None
        fields = [
            ("title", "Title"),
            ("body", "Description"),
            ("completion", "Outcome"),
            ("dependencies", "Prerequisites"),
        ]
        changed = [label for key, label in fields if old is None or old[key] != value[key]]
        if not changed:
            continue
        dependencies = [
            json.loads(
                db.execute(
                    "SELECT data FROM subtasks WHERE workspace_id=? AND task_id=? AND id=?",
                    (workspace, task, identity),
                ).fetchone()[0]
            )["key"]
            for identity in value["dependencies"]
        ]
        entries.append(
            f"### {value['key']}: {value['title']}\n\n"
            + ("Added subtask." if old is None else "Updated " + ", ".join(changed).lower() + ".")
            + f"\n\n{value['body']}\n\nOutcome: {value['completion']} · Prerequisites: "
            + (", ".join(dependencies) or "None")
        )
    return "\n\n".join(entries)
