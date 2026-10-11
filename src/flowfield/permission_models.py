"""Harness tool decisions, separate from task questions and code-result approval."""

from typing import Literal

from pydantic import Field

from flowfield.agent_models import AgentRecord, AgentRole


class PermissionOption(AgentRecord):
    id: str = Field(min_length=1, max_length=200)
    # Native choices can include an entire command prefix. Preserve that scope;
    # presentation text shares the bounded public-detail budget, not ID limits.
    label: str = Field(min_length=1, max_length=16000)
    kind: Literal["allow_once", "allow_always", "reject_once", "reject_always"]


class PermissionRecord(AgentRecord):
    id: str
    project_id: str | None = None
    workspace_id: str | None = None
    role: AgentRole
    task_id: str | None = None
    run_id: str | None = None
    conversation_id: str | None = None
    binding: str
    turn_id: str
    tool_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    details: str = Field(default="", max_length=16000)
    options: list[PermissionOption] = Field(min_length=1, max_length=16)
    revision: int = 1
    status: Literal["pending", "answered", "expired", "cancelled"] = "pending"
    answer: str | None = None
    created_at: str
    updated_at: str
    expires_at: str
    released_at: str | None = None


class PermissionAnswer(AgentRecord):
    expected_revision: int = Field(ge=1)
    option_id: str = Field(min_length=1, max_length=200)


class PermissionPage(AgentRecord):
    pending: list[PermissionRecord] = Field(default_factory=list)
    items: list[PermissionRecord]
    next_before: int | None = None
