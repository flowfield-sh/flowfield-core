"""Exactly bound task input."""

from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from flowfield.execution_models import Record
from flowfield.project_config import Identifier
from flowfield.subtasks import SubtaskIdentifier


class ReplyBinding(Record):
    task_revision: int
    agreement_revision: int
    result_id: str | None = None
    result_revision: int | None = None
    question_id: str | None = None
    question_revision: int | None = None


class ReplyFields(Record):
    id: Identifier = Field(default_factory=lambda: uuid4().hex)
    binding: ReplyBinding
    body: str = Field(min_length=1, max_length=8000)
    author: str = Field(default="human", min_length=1, max_length=200)
    subtask_id: SubtaskIdentifier | None = Field(
        default=None,
        description="For changes, correct this subtask and its dependents; omitted corrects all.",
    )


class ReplyCreate(ReplyFields):
    action: Literal["changes", "answer"]

    @model_validator(mode="after")
    def correction_scope(self) -> "ReplyCreate":
        if self.subtask_id and self.action != "changes":
            raise ValueError("A subtask scope applies only to requested changes.")
        return self


class Reply(ReplyFields):
    action: Literal["changes", "answer", "observation"]
    workspace_id: str
    task_id: str
    created_at: str
    run_id: str | None = None
    status: Literal["pending", "assigned", "recorded", "cancelled"] = "pending"
