"""Broad phases of agent work; these never grant completion or execution authority."""

from typing import Literal, Self

from pydantic import Field, model_validator

from flowfield.execution_models import Record
from flowfield.project_config import Identifier


class Stage(Record):
    id: Identifier
    title: str = Field(
        min_length=1,
        max_length=80,
        description="Short phase, such as Explore, Implement or Verify; not a file/task checklist.",
    )
    outcome: str = Field(
        min_length=1,
        max_length=600,
        description="Stable intended outcome of this phase. Progress evidence belongs in reason.",
    )
    status: Literal["planned", "active", "completed"] = "planned"


class StageChange(Record):
    expected_revision: int = Field(ge=0)
    stages: list[Stage] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def sequence(self) -> Self:
        if len({s.id for s in self.stages}) != len(self.stages):
            raise ValueError("Stage identifiers must be unique.")
        if sum(s.status == "active" for s in self.stages) > 1:
            raise ValueError("Only one stage can be active.")
        return self


class StageUpdate(StageChange):
    agreement_revision: int = Field(ge=1)


class StagePlan(Record):
    project_id: str
    task_id: str
    revision: int = 0
    agreement_revision: int
    stages: list[Stage] = Field(default_factory=list)
    reason: str = ""
    author: str = "coordinator"
    run_id: str | None = None
    created_at: str | None = None
