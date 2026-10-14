"""Exact milestone promotion evidence and separate human delivery authority."""

from typing import Literal

from pydantic import Field

from flowfield.execution_models import CheckResult, Record
from flowfield.integration_models import CheckoutBinding


class MilestonePromotion(Record):
    id: str
    workspace_id: str
    milestone_id: str
    verification_id: str
    project_id: str
    source_branch: str
    source_commit: str
    target_branch: str
    target_before: str
    settings_revision: int
    checkout: CheckoutBinding
    status: Literal[
        "preparing", "ready", "approved", "applying", "delivered", "blocked", "stale", "failed"
    ] = "preparing"
    candidate_commit: str | None = None
    workspace: str | None = None
    setup_checks: list[CheckResult] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    problem: str | None = None
    problem_code: str | None = None
    created_at: str
    validation_started_at: str | None = None
    approved_at: str | None = None
    approved_by: str | None = None
    apply_started_at: str | None = None
    delivered_at: str | None = None
