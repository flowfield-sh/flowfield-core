"""Exact inputs and constrained proposals for automatic guidance reconciliation."""

from typing import Literal

from pydantic import Field, model_validator

from flowfield.document_models import DocumentSnapshot
from flowfield.execution_models import Record
from flowfield.reviewer_settings import EffectiveReviewer
from flowfield.subtasks import SubtaskIdentifier


class GuidanceSubtaskEdit(Record):
    id: SubtaskIdentifier
    body: str | None = Field(default=None, min_length=1, max_length=200_000)
    dependencies: list[SubtaskIdentifier] | None = Field(default=None, max_length=100)


class GuidanceBinding(Record):
    task_revision: int
    agreement_revision: int
    documents: list[DocumentSnapshot]
    previous_documents: list[DocumentSnapshot]
    reviewer: EffectiveReviewer
    result_revision: int | None = None
    run_id: str | None = None
    run_revision: int | None = None
    candidate_commit: str | None = None
    task_context: str = ""
    result_context: str = ""


class GuidanceDecision(Record):
    action: Literal["adopt", "ask_human"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=8000)
    request: str = Field(default="", max_length=8000)
    body: str | None = Field(default=None, max_length=200_000)
    acceptance_criteria: str | None = Field(default=None, max_length=200_000)
    dependencies: list[str] | None = Field(default=None, max_length=100)
    subtasks: list[GuidanceSubtaskEdit] = Field(default_factory=list, max_length=100)
    rework: bool = False

    @model_validator(mode="after")
    def valid_decision(self) -> "GuidanceDecision":
        if not self.summary.strip() or not self.evidence.strip():
            raise ValueError("Describe the reconciliation and its evidence.")
        if (self.action == "ask_human" or self.rework) and not self.request.strip():
            raise ValueError("State the question or the required implementation change.")
        if self.action == "ask_human" and (
            self.body is not None
            or self.acceptance_criteria is not None
            or self.dependencies is not None
            or self.subtasks
            or self.rework
        ):
            raise ValueError("Resolve the question before proposing task changes.")
        return self
