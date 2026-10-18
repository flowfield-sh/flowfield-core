"""Canonical project/task questions and atomic application of saved answers."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from flowfield.activity import TaskKey
from flowfield.errors import ApplicationError
from flowfield.input_delivery import InputDelivery, question_delivery
from flowfield.project_config import Identifier, Title

if TYPE_CHECKING:
    from flowfield.application import Workspace

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
Status = Literal["open", "answered", "assigned", "applied", "withdrawn"]


class QuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Identifier = Field(default_factory=lambda: uuid4().hex)
    task_id: TaskKey | None = None
    affected_task_ids: list[TaskKey] = Field(default_factory=list, max_length=50)
    question: Title = Field(
        description="Short question title (at most 200 characters); put details in context."
    )
    context: Text
    recommendation: Text
    choices: list[Title] = Field(default_factory=list, max_length=5)
    blocking_scope: Text | None = None
    author: Title = "human"

    @model_validator(mode="after")
    def scope(self) -> Self:
        if self.task_id is not None and self.affected_task_ids:
            raise ValueError(
                "Use task_id for a task question or affected_task_ids for a project question."
            )
        if self.task_id is None and self.blocking_scope and not self.affected_task_ids:
            raise ValueError("A blocking project question must name affected tasks.")
        return self


class QuestionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    answer: Text
    author: Title = "human"


class AnswerRetract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    author: Title = "human"


class QuestionFollowUp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    question: Title
    context: Text
    recommendation: Text
    choices: list[Title] = Field(default_factory=list, max_length=5)
    author: Title = "human"


class QuestionTaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: TaskKey
    expected_revision: int = Field(ge=1)
    body: Annotated[str, StringConstraints(max_length=400_020)] | None = None


class QuestionApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    expected_task_revision: int | None = Field(default=None, ge=1)
    expected_workspace_revision: int | None = Field(default=None, ge=1)
    description: Annotated[str, StringConstraints(max_length=200_000)] | None = None
    task_updates: list[QuestionTaskUpdate] = Field(default_factory=list, max_length=50)
    body: Annotated[str, StringConstraints(max_length=400_020)] | None = None
    decision: Text
    author: Title = "human"


class QuestionWithdraw(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    reason: Text
    author: Title = "human"


class Question(QuestionCreate):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    workspace_id: str
    task_key: str | None = None
    affected_task_keys: list[str] = Field(default_factory=list)
    status: Status = "open"
    revision: int = 1
    answer: str | None = None
    answer_count: int = 0
    origin_run_id: str | None = None
    origin_review_id: str | None = None
    continuation_run_id: str | None = None
    consumed_answer_revision: int | None = None
    correction_question_id: str | None = None
    delivery: InputDelivery | None = None
    decision: str | None = None
    applied_task_revision: int | None = None
    applied_workspace_revision: int | None = None
    applied_task_revisions: dict[str, int] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    updated_by: str


class QuestionReference(BaseModel):
    id: str
    task_id: str | None
    revision: int
    question: str
    status: Status
    blocking_scope: str | None
    delivery: InputDelivery | None = None


def _pending_question_rows(
    db: sqlite3.Connection, workspace_id: str, task_id: str | None, *, blocking_only: bool
) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id,task_id,status,json_extract(data,'$.revision') AS revision, "
        "json_extract(data,'$.question') AS question, "
        "json_extract(data,'$.blocking_scope') AS blocking_scope, "
        "json_extract(data,'$.affected_task_ids') AS affected_task_ids FROM questions "
        "WHERE workspace_id=? AND status IN ('open','answered') "
        + (
            "AND (task_id=? OR EXISTS (SELECT 1 FROM json_each(questions.data, "
            "'$.affected_task_ids') WHERE value=?)) "
            if task_id is not None
            else ""
        )
        + ("AND json_extract(data,'$.blocking_scope') IS NOT NULL " if blocking_only else "")
        + "ORDER BY number",
        (workspace_id, task_id, task_id) if task_id is not None else (workspace_id,),
    ).fetchall()


def pending_questions(
    db: sqlite3.Connection, workspace_id: str, task_id: str, *, blocking_only: bool = False
) -> list[QuestionReference]:
    return [
        QuestionReference(**dict(row), delivery=question_delivery(db, workspace_id, row["id"]))
        for row in _pending_question_rows(db, workspace_id, task_id, blocking_only=blocking_only)
    ]


def blocking_questions(
    db: sqlite3.Connection, workspace_id: str, task_id: str
) -> list[QuestionReference]:
    return pending_questions(db, workspace_id, task_id, blocking_only=True)


def project_blocking_questions(
    db: sqlite3.Connection, workspace_id: str
) -> dict[str, list[QuestionReference]]:
    result: dict[str, list[QuestionReference]] = {}
    for row in _pending_question_rows(db, workspace_id, None, blocking_only=True):
        question = QuestionReference(
            **dict(row), delivery=question_delivery(db, workspace_id, row["id"])
        )
        tasks = set(json.loads(row["affected_task_ids"] or "[]"))
        if row["task_id"] is not None:
            tasks.add(row["task_id"])
        for task in tasks:
            result.setdefault(task, []).append(question)
    return result


class Questions:
    def __init__(self, workspace: "Workspace"):
        self.workspace = workspace

    def _get(
        self, db: sqlite3.Connection, workspace_id: str, identity: str, revision: int | None = None
    ) -> Question:
        row = db.execute(
            "SELECT data FROM questions WHERE workspace_id=? AND id=?"
            if revision is None
            else (
                "SELECT data FROM question_revisions "
                "WHERE workspace_id=? AND question_id=? AND revision=?"
            ),
            (workspace_id, identity) if revision is None else (workspace_id, identity, revision),
        ).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Question not found in this project.", 404)
        return Question.model_validate_json(row[0])

    def get(self, workspace_id: str, identity: str, revision: int | None = None) -> Question:
        with self.workspace.connection() as db:
            result = self._get(db, workspace_id, identity, revision)
            if revision is None:
                result.delivery = question_delivery(db, workspace_id, identity)
            return result

    def counts(self, db: sqlite3.Connection, workspace_id: str) -> dict[str, int]:
        from flowfield.attention import attention_counts

        return {
            "needs_you_count": attention_counts(db, workspace_id).get("action", 0),
            "awaiting_application_count": db.execute(
                "SELECT count(*) FROM questions WHERE workspace_id=? AND status='answered' "
                "AND json_extract(data,'$.origin_run_id') IS NULL "
                "AND json_extract(data,'$.origin_review_id') IS NULL",
                (workspace_id,),
            ).fetchone()[0],
        }

    def list(
        self,
        workspace_id: str,
        *,
        status: str = "active",
        task_id: str | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        from flowfield.reads import PAGE_BYTES, excerpt, page, validate_page

        validate_page(limit, after)
        if status not in (
            "active",
            "resolved",
            "open",
            "answered",
            "assigned",
            "applied",
            "withdrawn",
        ):
            raise ApplicationError(
                "invalid_request",
                "Choose active, resolved, open, answered, assigned, applied, or withdrawn.",
            )
        with self.workspace.connection() as db:
            self.workspace._workspace(db, workspace_id)
            where = "workspace_id=? AND number>?"
            args: list[Any] = [workspace_id, after or 0]
            if task_id:
                task_id = self.workspace._activity_scope(db, workspace_id, task_id)
                where += (
                    " AND (task_id=? OR EXISTS (SELECT 1 FROM "
                    "json_each(questions.data, '$.affected_task_ids') WHERE value=?))"
                )
                args.extend([task_id, task_id])
            if status in ("active", "resolved"):
                where += (
                    " AND status IN ('open','answered')"
                    if status == "active"
                    else " AND status IN ('assigned','applied','withdrawn')"
                )
            else:
                where += " AND status=?"
                args.append(status)
            rows = db.execute(
                "SELECT number, data FROM questions WHERE " + where + " ORDER BY number LIMIT ?",
                (*args, limit + 1),
            ).fetchall()
            items = []
            for row in rows:
                question = Question.model_validate_json(row["data"])
                items.append(
                    excerpt(
                        {
                            "number": row["number"],
                            "delivery": delivery.model_dump()
                            if (delivery := question_delivery(db, workspace_id, question.id))
                            else None,
                            **question.model_dump(
                                include={
                                    "id",
                                    "task_id",
                                    "task_key",
                                    "affected_task_ids",
                                    "affected_task_keys",
                                    "question",
                                    "status",
                                    "revision",
                                    "blocking_scope",
                                    "updated_at",
                                    "updated_by",
                                }
                            ),
                        },
                        500,
                    )
                )
            return {
                **page(items, limit, "number", budget=PAGE_BYTES - 200),
                **self.counts(db, workspace_id),
            }

    def history(
        self, workspace_id: str, identity: str, *, before: int | None = None, limit: int = 10
    ) -> dict[str, Any]:
        from flowfield.reads import excerpt, page, validate_page

        validate_page(limit, before)
        with self.workspace.connection() as db:
            self._get(db, workspace_id, identity)
            rows = db.execute(
                "SELECT data FROM question_revisions WHERE workspace_id=? AND question_id=? "
                "AND revision<? ORDER BY revision DESC LIMIT ?",
                (workspace_id, identity, before or 2**63 - 1, limit + 1),
            ).fetchall()
            return page(
                [excerpt(Question.model_validate_json(r[0]).model_dump(), 1000) for r in rows],
                limit,
                "revision",
            )

    def _save(self, db: sqlite3.Connection, question: Question, event: str) -> Question:
        data = question.model_dump_json(exclude={"delivery"})
        db.execute(
            "UPDATE questions SET status=?, data=? WHERE workspace_id=? AND id=?",
            (question.status, data, question.workspace_id, question.id),
        )
        db.execute(
            "INSERT INTO question_revisions VALUES (?, ?, ?, ?)",
            (question.workspace_id, question.id, question.revision, data),
        )
        scopes = [question.task_id] if question.task_id else [None, *question.affected_task_ids]
        for task_id in scopes:
            self.workspace._insert_activity(
                db,
                question.workspace_id,
                task_id,
                uuid4().hex,
                "event",
                event,
                question.updated_by,
                question.updated_at,
                question_id=question.id,
            )
        return question

    def _change(
        self, current: Question, expected_revision: int, author: str, **changes: Any
    ) -> Question:
        self.workspace._current(current.revision, expected_revision)
        return current.model_copy(
            update={
                **changes,
                "revision": current.revision + 1,
                "updated_by": author,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    def ask(self, workspace_id: str, request: QuestionCreate) -> Question:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            return self._ask(db, workspace_id, request)

    def _ask(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        request: QuestionCreate,
        *,
        origin_run_id: str | None = None,
        origin_review_id: str | None = None,
    ) -> Question:
        self.workspace._workspace(db, workspace_id)
        task = self.workspace._task(db, workspace_id, request.task_id) if request.task_id else None
        affected = [
            self.workspace._task(db, workspace_id, key) for key in request.affected_task_ids
        ]
        affected = sorted({item.id: item for item in affected}.values(), key=lambda item: item.id)
        for item in [task] if task else affected:
            if item.archived or item.status == "done":
                raise ApplicationError(
                    "inactive_task",
                    "Ask about active tasks; restore or reopen them first.",
                    409,
                )
        normalized = request.model_copy(
            update={
                "task_id": task.id if task else None,
                "affected_task_ids": [item.id for item in affected],
            }
        )
        if db.execute(
            "SELECT 1 FROM questions WHERE workspace_id=? AND id=?", (workspace_id, request.id)
        ).fetchone():
            original = self._get(db, workspace_id, request.id, 1)
            if (
                original.model_dump(include=set(QuestionCreate.model_fields))
                == normalized.model_dump()
            ):
                return self._get(db, workspace_id, request.id)
            raise ApplicationError("question_conflict", "Question ID already used.", 409)
        stamp = datetime.now(UTC).isoformat()
        question = Question(
            **normalized.model_dump(),
            workspace_id=workspace_id,
            origin_run_id=origin_run_id,
            origin_review_id=origin_review_id,
            task_key=task.key if task else None,
            affected_task_keys=[item.key for item in affected],
            created_at=stamp,
            updated_at=stamp,
            updated_by=request.author,
        )
        db.execute(
            "INSERT INTO questions (workspace_id,id,task_id,status,data) VALUES (?,?,?,?,?)",
            (workspace_id, question.id, question.task_id, "open", question.model_dump_json()),
        )
        return self._save(db, question, "Input requested: " + question.question)

    def answer(self, workspace_id: str, identity: str, request: QuestionAnswer) -> Question:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            return self._answer(db, workspace_id, identity, request)

    def _answer(
        self, db: sqlite3.Connection, workspace_id: str, identity: str, request: QuestionAnswer
    ) -> Question:
        current = self._get(db, workspace_id, identity)
        # A replay of the exact preceding write is a receipt, not another continuation.
        if (
            (current.origin_run_id or current.origin_review_id)
            and current.answer == request.answer
            and request.expected_revision
            in (
                current.revision,
                current.revision - 1,
                (current.consumed_answer_revision or 0) - 1,
            )
        ):
            current.delivery = question_delivery(db, workspace_id, identity)
            return current
        if current.continuation_run_id or current.consumed_answer_revision:
            raise ApplicationError(
                "answer_consumed",
                "This answer is already assigned to work. Send a correction as new input; "
                "the original answer cannot be replaced.",
                409,
            )
        if current.status not in ("open", "answered"):
            raise ApplicationError("question_closed", "This question is already resolved.", 409)
        self.workspace._current(current.revision, request.expected_revision)
        if current.status == "answered" and current.answer == request.answer:
            return current
        question = self._change(
            current,
            request.expected_revision,
            request.author,
            status="answered",
            answer=request.answer,
            answer_count=current.answer_count + 1,
        )
        self._save(
            db,
            question,
            "Answer sent."
            if current.origin_run_id or current.origin_review_id
            else "Answer saved; coordinator response needed.",
        )
        question.delivery = question_delivery(db, workspace_id, identity)
        return question

    def correct(self, workspace_id: str, identity: str, request: QuestionAnswer) -> Question:
        """A consumed answer is immutable; contradictory new input needs reconciliation."""
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            original = self._get(db, workspace_id, identity)
            if original.correction_question_id:
                correction = self._get(db, workspace_id, original.correction_question_id)
                if correction.answer == request.answer:
                    return correction
            self.workspace._current(original.revision, request.expected_revision)
            if not original.continuation_run_id:
                raise ApplicationError(
                    "answer_pending", "Edit the pending answer before assignment.", 409
                )
            correction = self._ask(
                db,
                workspace_id,
                QuestionCreate(
                    task_id=original.task_id,
                    question="Reconcile a correction to an earlier answer",
                    context=(
                        f"Correction to question {original.id}: {original.question[:500]}\n\n"
                        f"Read preserved answer revision {original.consumed_answer_revision} "
                        "before reconciling this new input."
                    ),
                    recommendation="Reconcile this input before continuing or approving work.",
                    blocking_scope="A correction needs coordinator reconciliation.",
                    author=request.author,
                ),
            )
            correction = self._change(
                correction,
                correction.revision,
                request.author,
                status="answered",
                answer=request.answer,
                answer_count=1,
            )
            self._save(db, correction, "Correction received; coordinator reconciliation needed.")
            original = self._change(
                original, original.revision, request.author, correction_question_id=correction.id
            )
            self._save(db, original, "Correction recorded as new input; original answer retained.")
            return correction

    def retract_answer(self, workspace_id: str, identity: str, request: AnswerRetract) -> Question:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            current = self._get(db, workspace_id, identity)
            if current.origin_run_id or current.origin_review_id:
                raise ApplicationError(
                    "managed_answer", "Edit pending input instead of retracting it.", 409
                )
            if current.status != "answered":
                raise ApplicationError(
                    "question_state", "Only an unapplied saved answer can be retracted.", 409
                )
            question = self._change(
                current, request.expected_revision, request.author, status="open", answer=None
            )
            return self._save(db, question, "Answer retracted; question reopened.")

    def follow_up(self, workspace_id: str, identity: str, request: QuestionFollowUp) -> Question:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            current = self._get(db, workspace_id, identity)
            if current.origin_run_id or current.origin_review_id:
                raise ApplicationError(
                    "managed_answer", "Ask a new question; preserve managed input history.", 409
                )
            if current.status != "answered":
                raise ApplicationError("question_state", "Follow up on a saved answer.", 409)
            question = self._change(
                current,
                request.expected_revision,
                request.author,
                **request.model_dump(exclude={"expected_revision", "author"}),
                status="open",
                answer=None,
            )
            return self._save(db, question, "Follow-up requested: " + question.question)

    def apply(self, workspace_id: str, identity: str, request: QuestionApply) -> Question:
        from flowfield.application import TaskEdit, TaskRevision

        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            current = self._get(db, workspace_id, identity)
            if current.origin_run_id or current.origin_review_id:
                raise ApplicationError(
                    "managed_answer",
                    "The service delivers this task answer. Reconcile changed scope explicitly.",
                    409,
                )
            if current.status != "answered":
                raise ApplicationError("question_state", "Save an answer before applying it.", 409)
            self.workspace._current(current.revision, request.expected_revision)
            if current.task_id is None:
                return self._apply_workspace(db, current, request)
            if request.expected_task_revision is None:
                raise ApplicationError(
                    "invalid_request", "Task application requires expected_task_revision."
                )
            if (
                request.expected_workspace_revision is not None
                or request.description is not None
                or request.task_updates
            ):
                raise ApplicationError(
                    "invalid_request",
                    "Workspace application fields do not apply to a task question.",
                )
            task = self.workspace._task(db, workspace_id, current.task_id)
            self.workspace._current(task.revision, request.expected_task_revision)
            if task.archived:
                raise ApplicationError(
                    "inactive_task", "Restore the task before applying an answer.", 409
                )
            patch_data: dict[str, Any] = {
                "expected_revision": task.revision,
                "author": request.author,
            }
            if request.body is not None:
                patch_data["body"] = request.body
            patch = TaskEdit(**patch_data)
            revised = TaskRevision(**self.workspace._patch(task, patch))
            question = self._change(
                current,
                request.expected_revision,
                request.author,
                status="applied",
                decision=request.decision,
                applied_task_revision=revised.revision,
            )
            if revised.revision != task.revision:
                self.workspace._apply_task(db, revised)
            self.workspace._insert_activity(
                db,
                workspace_id,
                task.id,
                uuid4().hex,
                "note",
                request.decision,
                request.author,
                question.updated_at,
                question_id=identity,
            )
            question.applied_task_revision = db.execute(
                "SELECT json_extract(data, '$.revision') FROM tasks WHERE workspace_id=? AND id=?",
                (workspace_id, task.id),
            ).fetchone()[0]
            return self._save(db, question, "Answer applied.")

    def _apply_workspace(
        self, db: sqlite3.Connection, current: Question, request: QuestionApply
    ) -> Question:
        from flowfield.application import TaskEdit, TaskRevision
        from flowfield.workspaces import WorkspaceEdit, Workspaces

        if request.expected_workspace_revision is None:
            raise ApplicationError(
                "invalid_request", "Workspace application requires expected_workspace_revision."
            )
        if request.expected_task_revision is not None or request.body is not None:
            raise ApplicationError(
                "invalid_request",
                "Use task_updates for affected tasks and description for workspace intent.",
            )
        revised_tasks = {}
        previous_revisions = {}
        for update in request.task_updates:
            task = self.workspace._task(db, current.workspace_id, update.task_id)
            if task.id in revised_tasks:
                raise ApplicationError(
                    "invalid_request", "Each affected task may appear only once."
                )
            if task.archived:
                raise ApplicationError(
                    "inactive_task", "Restore the task before applying an answer.", 409
                )
            patch_data: dict[str, Any] = {
                "expected_revision": update.expected_revision,
                "author": request.author,
            }
            if update.body is not None:
                patch_data["body"] = update.body
            patch = TaskEdit(**patch_data)
            revised_tasks[task.id] = TaskRevision(**self.workspace._patch(task, patch))
            previous_revisions[task.id] = task.revision
        if set(revised_tasks) != set(current.affected_task_ids):
            raise ApplicationError(
                "invalid_request", "Supply a fresh revision for every affected task, and no others."
            )
        workspace = Workspaces(self.workspace)._edit(
            db,
            current.workspace_id,
            WorkspaceEdit.model_validate(
                {
                    "expected_revision": request.expected_workspace_revision,
                    "author": request.author,
                    **(
                        {"description": request.description}
                        if request.description is not None
                        else {}
                    ),
                }
            ),
        )
        self.workspace._apply_tasks(
            db,
            [
                task
                for task in revised_tasks.values()
                if previous_revisions[task.id] != task.revision
            ],
        )
        question = self._change(
            current,
            request.expected_revision,
            request.author,
            status="applied",
            decision=request.decision,
            applied_workspace_revision=workspace.revision,
        )
        self.workspace._insert_activity(
            db,
            current.workspace_id,
            None,
            uuid4().hex,
            "event",
            request.decision,
            request.author,
            question.updated_at,
            question_id=current.id,
        )
        question.applied_task_revisions = {
            row[0]: row[1]
            for row in db.execute(
                "SELECT id, json_extract(data, '$.revision') FROM tasks WHERE workspace_id=?",
                (current.workspace_id,),
            )
            if row[0] in revised_tasks
        }
        return self._save(db, question, "Workspace answer applied.")

    def withdraw(self, workspace_id: str, identity: str, request: QuestionWithdraw) -> Question:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            current = self._get(db, workspace_id, identity)
            if current.status not in ("open", "answered"):
                raise ApplicationError("question_closed", "This question is already resolved.", 409)
            question = self._change(
                current,
                request.expected_revision,
                request.author,
                status="withdrawn",
                decision=request.reason,
            )
            return self._save(db, question, "Question withdrawn: " + request.reason)
