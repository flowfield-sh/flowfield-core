"""A bounded conversation projected from canonical evidence, not a second transcript."""

import json
import sqlite3
from typing import Literal

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import ACTIVE, Record


class ConversationItem(Record):
    id: str
    kind: Literal[
        "definition",
        "question",
        "answer",
        "activity",
        "attempt",
        "review",
        "result",
        "approval",
        "delivery",
        "reply",
    ]
    created_at: str
    author: str
    source_id: str
    revision: int | None = None
    excerpt: str
    total_chars: int


class ConversationPage(Record):
    items: list[ConversationItem]
    next_cursor: str | None = None


class ConversationSource(Record):
    item: ConversationItem
    text: str
    next_offset: int | None = None


class InputEligibility(Record):
    enabled: bool
    reason: str
    task_revision: int
    agreement_revision: int
    question_id: str | None = None
    question_revision: int | None = None
    run_id: str | None = None
    result_id: str | None = None
    result_revision: int | None = None
    pending_reply_id: str | None = None


# IDs identify evidence, not its position. Current run/result state is intentionally live;
# questions and definitions refer to exact immutable revisions. SQL pages before hydration.
PROJECTION = """
WITH definitions AS (
    SELECT *, lag(data) OVER (ORDER BY revision) AS previous FROM task_revisions
    WHERE workspace_id=:project AND task_id=:task
), exchanges AS (
    SELECT r.*, lag(r.data) OVER (PARTITION BY r.question_id ORDER BY r.revision) AS previous
    FROM question_revisions r JOIN questions q
        ON q.workspace_id=r.workspace_id AND q.id=r.question_id
    WHERE q.workspace_id=:project AND q.task_id=:task
), conversation AS (
    SELECT 'reply:'||id AS id, 'reply' AS kind, created_at,
        json_extract(data,'$.author') AS author, id AS source_id, NULL AS revision, data
    FROM task_replies WHERE workspace_id=:project AND task_id=:task
        AND json_extract(data,'$.action')!='answer'
    UNION ALL
    SELECT 'definition:'||revision AS id, 'definition' AS kind,
        json_extract(data,'$.updated_at') AS created_at,
        json_extract(data,'$.updated_by') AS author,
        :task AS source_id, revision, data
    FROM definitions WHERE previous IS NULL
        OR json_extract(data,'$.agreement_revision') !=
            json_extract(previous,'$.agreement_revision')
        OR json_extract(data,'$.status') != json_extract(previous,'$.status')
        OR json_extract(data,'$.reconciliation_reason') IS NOT
            json_extract(previous,'$.reconciliation_reason')
        OR json_extract(data,'$.archived') != json_extract(previous,'$.archived')
    UNION ALL
    SELECT 'question:'||question_id||':'||revision,
        CASE WHEN json_extract(data,'$.answer') IS NOT NULL THEN 'answer' ELSE 'question' END,
        json_extract(data,'$.updated_at'), json_extract(data,'$.updated_by'),
        question_id, revision, data
    FROM exchanges WHERE previous IS NULL
        OR json_extract(data,'$.answer') IS NOT json_extract(previous,'$.answer')
        OR (json_extract(data,'$.status') IN ('withdrawn','open') AND
            json_extract(data,'$.status') != json_extract(previous,'$.status'))
    UNION ALL
    SELECT 'activity:'||a.id, 'activity', a.created_at, a.author, a.id, NULL,
        json_object('kind',a.kind,'body',a.body,'supersedes',a.supersedes,
          'question_id',a.question_id,'task_revision',a.task_revision,
          'superseded_by',(SELECT id FROM activity b WHERE b.supersedes=a.id))
    FROM activity a WHERE workspace_id=:project AND task_id=:task AND kind!='event'
    UNION ALL
    SELECT 'attempt:'||id, 'attempt', json_extract(data,'$.created_at'), 'worker', id,
        json_extract(data,'$.revision'), data
    FROM runs WHERE workspace_id=:project AND task_id=:task
    UNION ALL
    SELECT 'review:'||id, 'review', json_extract(data,'$.created_at'), 'reviewer', id,
        json_extract(data,'$.revision'), data
    FROM task_reviews WHERE workspace_id=:project AND task_id=:task
    UNION ALL
    SELECT 'result:'||id, 'result', json_extract(data,'$.created_at'), 'service', id,
        json_extract(data,'$.revision'), data
    FROM result_versions WHERE workspace_id=:project AND task_id=:task
    UNION ALL
    SELECT 'approval:'||id, 'approval', json_extract(data,'$.approved_at'),
        coalesce(json_extract(data,'$.approved_by'),'human'), id,
        json_extract(data,'$.revision'), data
    FROM result_versions WHERE workspace_id=:project AND task_id=:task
        AND json_extract(data,'$.approved_at') IS NOT NULL
    UNION ALL
    SELECT 'delivery:'||id, 'delivery', json_extract(data,'$.completed_at'),
        'service', id, json_extract(data,'$.revision'), data
    FROM result_versions WHERE workspace_id=:project AND task_id=:task
        AND status='delivered' AND json_extract(data,'$.completed_at') IS NOT NULL
)
"""
COLUMNS = (
    "id,kind,created_at,author,source_id,revision,"
    "substr(data,1,400) AS excerpt,length(data) AS total_chars"
)


class Conversation:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _args(self, db: sqlite3.Connection, workspace_id: str, task_id: str) -> dict[str, str]:
        task = self.workspace._activity_scope(db, workspace_id, task_id)
        assert task
        return {"project": workspace_id, "task": task}

    def page(
        self, workspace_id: str, task_id: str, cursor: str | None = None, limit: int = 20
    ) -> ConversationPage:
        with self.workspace.connection() as db:
            return self._page(db, workspace_id, task_id, cursor, limit)

    def _page(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        task_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ConversationPage:
        if not 1 <= limit <= 30:
            raise ApplicationError("invalid_limit", "Choose 1–30 conversation items.")
        stamp, identity = "", ""
        if cursor:
            try:
                value = json.loads(cursor)
                if (
                    not isinstance(value, list)
                    or len(value) != 2
                    or not all(isinstance(s, str) for s in value)
                ):
                    raise ValueError
                stamp, identity = value
            except (ValueError, TypeError):
                raise ApplicationError("invalid_cursor", "Invalid conversation cursor.") from None
        args = self._args(db, workspace_id, task_id)
        rows = db.execute(
            PROJECTION + f"SELECT {COLUMNS} FROM conversation "
            "WHERE :stamp='' OR (created_at,id)<(:stamp,:identity) "
            "ORDER BY created_at DESC,id DESC LIMIT :limit",
            {**args, "stamp": stamp, "identity": identity, "limit": limit + 1},
        ).fetchall()
        items = [ConversationItem.model_validate(dict(row)) for row in rows[:limit]]
        return ConversationPage(
            items=items,
            next_cursor=json.dumps([items[-1].created_at, items[-1].id])
            if len(rows) > limit
            else None,
        )

    def source(
        self,
        workspace_id: str,
        task_id: str,
        item_id: str,
        offset: int = 0,
        expected_revision: int | None = None,
    ) -> ConversationSource:
        if offset < 0:
            raise ApplicationError("invalid_offset", "Offset must be nonnegative.")
        with self.workspace.connection() as db:
            args = self._args(db, workspace_id, task_id)
            row = db.execute(
                PROJECTION + f"SELECT {COLUMNS}, substr(data,:offset+1,6000) AS text "
                "FROM conversation WHERE id=:id",
                {**args, "id": item_id, "offset": offset},
            ).fetchone()
            if not row:
                raise ApplicationError(
                    "not_found", "Conversation item not found for this task.", 404
                )
            data = dict(row)
            text = data.pop("text")
            item = ConversationItem.model_validate(data)
            if item.kind in ("attempt", "review", "result", "approval", "delivery"):
                if (
                    offset > 0 or expected_revision is not None
                ) and item.revision != expected_revision:
                    raise ApplicationError(
                        "source_changed",
                        "Reread this source from offset 0 at its current revision.",
                        409,
                    )
            if offset > item.total_chars:
                raise ApplicationError("invalid_offset", "Offset exceeds source length.")
            return ConversationSource(
                item=item,
                text=text,
                next_offset=offset + len(text) if offset + len(text) < item.total_chars else None,
            )

    def eligibility(
        self, workspace_id: str, task_id: str, question_id: str | None = None
    ) -> InputEligibility:
        with self.workspace.connection() as db:
            args = self._args(db, workspace_id, task_id)
            return self._eligibility(db, args["project"], args["task"], question_id)

    def _eligibility(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        task_id: str,
        question_id: str | None = None,
    ) -> InputEligibility:
        """Recheck inside the submit transaction; a read receipt never authorizes a write."""
        task = json.loads(
            db.execute(
                "SELECT data FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id)
            ).fetchone()[0]
        )
        result = InputEligibility(
            enabled=True,
            reason="idle",
            task_revision=task["revision"],
            agreement_revision=task["agreement_revision"],
        )
        question = db.execute(
            "SELECT id,data FROM questions WHERE workspace_id=? AND task_id=? "
            "AND status='open' AND (? IS NULL OR id=?) ORDER BY number LIMIT 1",
            (workspace_id, task_id, question_id, question_id),
        ).fetchone()
        question_run = (
            db.execute(
                "SELECT subtask_id FROM runs WHERE workspace_id=? AND id=?",
                (workspace_id, json.loads(question[1]).get("origin_run_id")),
            ).fetchone()
            if question
            else None
        )
        subtask = question_run[0] if question_run else None
        run = db.execute(
            "SELECT id FROM runs WHERE workspace_id=? AND task_id=? AND status IN (?,?,?,?) "
            "AND (? IS NULL OR subtask_id IS ?)",
            (workspace_id, task_id, *ACTIVE, subtask, subtask),
        ).fetchone()
        version = db.execute(
            "SELECT data FROM result_versions WHERE workspace_id=? AND task_id=? "
            "ORDER BY version DESC LIMIT 1",
            (workspace_id, task_id),
        ).fetchone()
        if version:
            value = json.loads(version[0])
            result.result_id, result.result_revision = value["id"], value["revision"]
            if value["status"] in ("preparing", "delivering"):
                result.enabled, result.reason = False, "processing_result"
            elif value["completion"] == "report" and not (
                question
                and db.execute(
                    "SELECT 1 FROM task_reviews WHERE workspace_id=? AND task_id=? "
                    "AND status='waiting' AND id=? AND json_extract(data,'$.question_id')=?",
                    (
                        workspace_id,
                        task_id,
                        json.loads(question[1]).get("origin_review_id"),
                        question[0],
                    ),
                ).fetchone()
            ):
                from flowfield.result_actions import result_action
                from flowfield.result_models import ResultVersion

                if (
                    result_action(self.workspace, db, ResultVersion.model_validate(value)).owner
                    == "reviewer"
                ):
                    result.enabled, result.reason = False, "review_owns_result"
        if run:
            result.enabled, result.reason, result.run_id = False, "worker_owns_work", run[0]
        pending = db.execute(
            "SELECT id FROM task_replies WHERE workspace_id=? AND task_id=? "
            "AND json_extract(data,'$.status')='pending' ORDER BY created_at LIMIT 1",
            (workspace_id, task_id),
        ).fetchone()
        if pending:
            result.enabled, result.reason, result.pending_reply_id = (
                False,
                "reply_pending",
                pending[0],
            )
        if task["archived"] or task["reconciliation_reason"]:
            result.enabled, result.reason = False, "reconcile_task"
        if db.execute(
            "SELECT 1 FROM task_reviews WHERE workspace_id=? AND task_id=? "
            "AND status IN ('queued','preparing','running','uncertain')",
            (workspace_id, task_id),
        ).fetchone():
            result.enabled, result.reason = False, "review_owns_result"
        if question:
            result.question_id, result.question_revision = (
                question[0],
                json.loads(question[1])["revision"],
            )
            if result.enabled:
                result.reason = "answer_expected"
        elif result.enabled:
            answered = db.execute(
                "SELECT id,data FROM questions WHERE workspace_id=? AND task_id=? "
                "AND status='answered' AND (? IS NULL OR id=?) ORDER BY number LIMIT 1",
                (workspace_id, task_id, question_id, question_id),
            ).fetchone()
            if answered:
                from flowfield.input_delivery import question_delivery

                delivery = question_delivery(db, workspace_id, answered[0])
                if delivery is None or delivery.can_edit:
                    result.question_id = answered[0]
                    result.question_revision = json.loads(answered[1])["revision"]
                    result.reason = "answer_editable"
                else:
                    result.enabled, result.reason = False, "answer_pending"
        return result
