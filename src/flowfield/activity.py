"""Durable activity contracts, independent of clients and harnesses."""

from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from flowfield.project_config import Identifier, Title

EntryKind = Literal["note", "handoff", "event"]
TaskKey = Annotated[
    str, StringConstraints(pattern=r"^(?:[a-z0-9][a-z0-9_-]{0,63}|[Tt]-[1-9][0-9]*)$")
]


class ActivityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Identifier = Field(default_factory=lambda: uuid4().hex)
    task_id: TaskKey
    kind: Literal["note", "handoff"] = "note"
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200_000)]
    author: Title = "human"
    supersedes: Identifier | None = None
    expected_task_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def scope_and_kind(self) -> Self:
        if self.supersedes is not None and self.kind == "note":
            raise ValueError("Notes cannot supersede entries.")
        if self.kind == "handoff":
            if self.expected_task_revision is None or "supersedes" not in self.model_fields_set:
                raise ValueError(
                    "Handoffs require expected_task_revision and supersedes (null for the first)."
                )
        elif self.expected_task_revision is not None:
            raise ValueError("Only handoffs use expected_task_revision.")
        return self


class ActivityEntry(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    sequence: int
    id: str
    workspace_id: str
    task_id: str | None
    kind: EntryKind
    body: str
    author: str
    created_at: str
    supersedes: str | None = None
    superseded_by: str | None = None
    task_revision: int | None = None
    question_id: str | None = None


class ActivityPage(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    items: list[ActivityEntry]
    next_cursor: int | None = None


# superseded_by is derived so there is only one authoritative replacement link.
ACTIVITY_SELECT = """
SELECT a.*, replacement.id AS superseded_by FROM activity a
LEFT JOIN activity replacement ON replacement.supersedes = a.id
"""
