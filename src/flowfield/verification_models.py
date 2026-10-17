"""Exact whole-milestone evidence, independent of task completion and delivery authority."""

from typing import Literal

from pydantic import BaseModel, Field

from flowfield.actors import Actor
from flowfield.output_models import OutputFile
from flowfield.promotion_models import MilestonePromotion
from flowfield.reviewer_settings import EffectiveReviewer
from flowfield.verification_report import (
    CriterionFinding as CriterionFinding,
)
from flowfield.verification_report import (
    VerificationInput,
)
from flowfield.verification_report import (
    VerificationReport as VerificationReport,
)


class VerificationPrepare(VerificationInput):
    expected_revision: int = Field(ge=1)
    project_id: str
    task_id: str
    expected_task_revision: int = Field(ge=1)
    checks: list[str] = Field(min_length=1, max_length=50)
    author: str = Field(default="coordinator", min_length=1, max_length=200)


class VerificationReview(VerificationInput):
    expected_revision: int = Field(ge=1)
    action: Literal["accept", "request_changes", "retry_promotion"]
    note: str = Field(default="", max_length=8000)
    author: str = Field(default="human", min_length=1, max_length=200)


class DeliveredTask(VerificationInput):
    project_id: str
    task_id: str
    task_key: str
    revision: int
    agreement_revision: int
    result_id: str
    result_revision: int
    settings_revision: int | None
    commit: str | None
    output_ids: list[str] = Field(default_factory=list)
    target_branch: str | None
    reconciliation_reason: str | None = None


class DependencyAssessment(VerificationInput):
    task_id: str
    check: int = Field(ge=1, le=50)
    evidence: str = Field(min_length=1, max_length=4000)


class DependencyResolution(DependencyAssessment):
    revision: int


VerificationStatus = Literal[
    "preparing",
    "verifying",
    "ready",
    "failed",
    "stale",
    "accepted",
    "changes_requested",
    "promoting",
    "delivery_blocked",
]


class MilestoneVerificationSummary(BaseModel):
    id: str
    status: VerificationStatus
    checks_passed: int | None
    checks_total: int
    review_status: str | None = None


class MilestoneVerification(VerificationInput):
    id: str
    workspace_id: str
    milestone_id: str
    milestone_key: str
    milestone_revision: int
    title: str
    criteria: str
    checks: list[str]
    tasks: list[DeliveredTask]
    project_id: str
    task_id: str | None = None
    task_key: str | None = None
    agreement_revision: int | None = None
    revision: int = 1
    status: VerificationStatus = "verifying"
    current: bool = True
    report: VerificationReport | None = None
    summary: str = ""
    dependency_resolutions: list[DependencyResolution] = Field(default_factory=list)
    limitations: str = ""
    result_id: str | None = None
    created_at: str
    updated_at: str
    author: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str = ""
    problem: str | None = None
    promotion_id: str | None = None
    # Read projection; the independent promotion record owns delivery authority.
    promotion: MilestonePromotion | None = None
    task_titles: dict[str, str] = Field(default_factory=dict)
    files: list[OutputFile] = Field(default_factory=list)
    purpose: Literal["task", "review"] = "task"
    plan_revision: int | None = None
    reviewer: EffectiveReviewer | None = None
    review_id: str | None = None
    instructions: str = ""
    review_status: str | None = None


class MilestoneTestingNote(BaseModel):
    id: str
    project_id: str
    task_key: str
    result_id: str
    body: str
    actor: Actor
    created_at: str


class MilestoneTestingPage(BaseModel):
    items: list[MilestoneTestingNote]
    next_offset: int | None = None
