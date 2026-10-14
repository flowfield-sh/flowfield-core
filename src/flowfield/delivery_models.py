"""Owned milestone destinations, separate from project defaults and final approval."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from flowfield.integration_models import CheckoutBinding


class DeliveryConfigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    project_id: str
    branch: str | None = Field(default=None, min_length=1, max_length=200)


class MilestoneDelivery(BaseModel):
    id: str
    workspace_id: str
    milestone_id: str
    project_id: str
    branch: str
    destination_branch: str
    base_commit: str
    path: str
    checkout: CheckoutBinding | None = None
    status: Literal["creating", "ready", "blocked"] = "creating"
    problem: str | None = None
    created_at: str
