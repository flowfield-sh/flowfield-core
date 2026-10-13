"""Small immutable milestone bindings shared by preparation and source freshness."""

from typing import Literal

from pydantic import BaseModel, Field


class MilestoneBinding(BaseModel):
    workspace_id: str
    id: str
    key: str
    title: str
    revision: int = Field(ge=1)
    origin: Literal["preparation", "upgrade"] = "preparation"


class MilestoneUpdate(BaseModel):
    workspace_id: str
    id: str
    key: str
    title: str
    prepared_revision: int
    latest_revision: int
