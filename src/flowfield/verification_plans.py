"""Coordinator-authored verification procedures saved before contributions run."""

import sqlite3
from typing import Any

from pydantic import Field, model_validator

from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.milestones import Milestones


class VerificationPlanEdit(Record):
    expected_revision: int = Field(ge=1)
    milestone_revision: int = Field(ge=1)
    checks: list[str] = Field(min_length=1, max_length=50)
    instructions: str = Field(min_length=1, max_length=8000)
    author: str = Field(default="coordinator", min_length=1, max_length=200)

    @model_validator(mode="after")
    def complete(self) -> "VerificationPlanEdit":
        if not self.instructions.strip() or any(
            not c.strip() or len(c) > 4000 for c in self.checks
        ):
            raise ValueError("Give observable checks and bounded procedures for all criteria.")
        return self


class VerificationPlan(Record):
    workspace_id: str
    milestone_id: str
    revision: int = 1
    milestone_revision: int
    checks: list[str]
    instructions: str
    author: str
    updated_at: str


def read(db: sqlite3.Connection, workspace: str, milestone: str) -> VerificationPlan | None:
    row = db.execute(
        "SELECT data FROM milestone_review_plans WHERE workspace_id=? AND milestone_id=? "
        "ORDER BY revision DESC LIMIT 1",
        (workspace, milestone),
    ).fetchone()
    return VerificationPlan.model_validate_json(row[0]) if row else None


class VerificationPlans:
    def __init__(self, store: Workspace):
        self.store = store

    def get(self, workspace: str, milestone: str) -> VerificationPlan | None:
        with self.store.connection() as db:
            value = Milestones.read(db, workspace, milestone)
            return read(db, workspace, value.id)

    def context(
        self,
        workspace: str,
        milestone: str,
        *,
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        value = self.get(workspace, milestone)
        if not value:
            return {"plan": None}
        if offset < 0 or not 1 <= limit <= 4000 or (offset and revision is None):
            raise ApplicationError("invalid_request", "Use bounded pages and the saved revision.")
        if revision is not None:
            self.store._current(value.revision, revision)
        body = value.model_dump_json()
        if offset > len(body):
            raise ApplicationError("invalid_offset", "Offset exceeds plan length.")
        end = min(len(body), offset + limit)
        return {
            "revision": value.revision,
            "text": body[offset:end],
            "total_characters": len(body),
            "next_offset": end if end < len(body) else None,
        }

    def save(
        self, workspace: str, milestone: str, request: VerificationPlanEdit
    ) -> VerificationPlan:
        with self.store.connection(write=True) as db:
            current = Milestones.read(db, workspace, milestone)
            self.store._current(current.revision, request.milestone_revision)
            if not current.acceptance_criteria.strip():
                raise ApplicationError(
                    "criteria_required",
                    "Agree milestone criteria before planning verification.",
                    409,
                )
            previous = read(db, workspace, current.id)
            self.store._current(previous.revision if previous else 1, request.expected_revision)
            value = VerificationPlan(
                workspace_id=workspace,
                milestone_id=current.id,
                revision=previous.revision + 1 if previous else 2,
                milestone_revision=current.revision,
                checks=request.checks,
                instructions=request.instructions,
                author=request.author,
                updated_at=now(),
            )
            db.execute(
                "INSERT INTO "
                "milestone_review_plans(workspace_id,milestone_id,revision,data) VALUES "
                "(?,?,?,?)",
                (workspace, current.id, value.revision, value.model_dump_json()),
            )
            delivery = db.execute(
                "SELECT project_id,json_extract(data,'$.review_plan_question_id') "
                "FROM milestone_deliveries WHERE workspace_id=? AND milestone_id=?",
                (workspace, current.id),
            ).fetchone()
            if delivery and delivery[1]:
                from flowfield.questions import Questions

                questions = Questions(self.store)
                question = questions._get(db, delivery[0], delivery[1])
                if question.status in ("open", "answered"):
                    question = questions._change(
                        question, question.revision, "flowfield", status="withdrawn"
                    )
                    questions._save(
                        db,
                        question,
                        "The Coordinator saved the bound verification plan; "
                        "earlier answers are retained.",
                    )
            return value
