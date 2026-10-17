"""Exactly bound task input."""

from typing import Literal
from uuid import uuid4

from pydantic import Field

from flowfield.execution_models import Record
from flowfield.project_config import Identifier


class ReplyBinding(Record):
    task_revision: int
    agreement_revision: int
    result_id: str | None = None
    result_revision: int | None = None
    question_id: str | None = None
    question_revision: int | None = None


class MilestoneObservationBinding(Record):
    verification_id: str
    verification_revision: int = Field(ge=1)
    candidate_commit: str = Field(min_length=40, max_length=64)


class ReplyFields(Record):
    id: Identifier = Field(default_factory=lambda: uuid4().hex)
    binding: ReplyBinding
    body: str = Field(min_length=1, max_length=8000)
    author: str = Field(default="human", min_length=1, max_length=200)
    milestone: MilestoneObservationBinding | None = Field(
        default=None,
        description="For human testing of a combined milestone, bind the exact verification "
        "and candidate from get_milestone_verification. Only for action=observation.",
    )


class ReplyCreate(ReplyFields):
    action: Literal["changes", "answer", "observation"]


class Reply(ReplyFields):
    action: Literal["changes", "answer", "observation"]
    project_id: str
    task_id: str
    created_at: str
    run_id: str | None = None
    status: Literal["pending", "assigned", "recorded", "cancelled"] = "pending"
