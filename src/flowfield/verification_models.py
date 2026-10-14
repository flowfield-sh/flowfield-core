"""Exact whole-milestone evidence, independent of task completion and delivery authority."""

from typing import Literal

from pydantic import BaseModel, Field

from flowfield.promotion_models import MilestonePromotion
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
    commit: str
    target_branch: str | None


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
    task_id: str
    task_key: str
    agreement_revision: int
    revision: int = 1
    status: VerificationStatus = "verifying"
    current: bool = True
    report: VerificationReport | None = None
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
