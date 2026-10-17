"""Durable conversation evidence; native sessions are not application identities."""

from typing import Literal

from pydantic import AliasChoices, Field

from flowfield.agent_models import AgentRecord, EffectiveAgent
from flowfield.attention import RequestKind
from flowfield.harness_models import HarnessLaunch
from flowfield.project_config import Identifier
from flowfield.run_activity import ContextUsage, RunActivityPage


class CoordinatorConversation(AgentRecord):
    welcome: str = ""
    id: str
    number: int
    created_at: str


class CoordinatorTaskSelection(AgentRecord):
    project_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=200)
    task_revision: int = Field(ge=1)
    result_id: str | None = Field(default=None, min_length=1, max_length=200)


class CoordinatorTaskContext(CoordinatorTaskSelection):
    key: str
    title: str


class CoordinatorViewSelection(AgentRecord):
    surface: Literal[
        "board",
        "inbox",
        "archive",
        "projects",
        "project",
        "milestones",
        "milestone",
        "task",
        "documents",
        "files",
        "document",
        "request",
    ]
    project_id: Identifier | None = None
    milestone_id: Identifier | None = None
    milestone_revision: int | None = Field(default=None, ge=1)
    verification_id: Identifier | None = None
    label_id: Identifier | None = None
    without_milestone: bool = False
    document_id: Identifier | None = None
    document_revision: int | None = Field(default=None, ge=1)
    request_kind: RequestKind | None = None
    request_id: Identifier | None = None


class CoordinatorViewContext(CoordinatorViewSelection):
    workspace_name: str
    workspace_revision: int
    project_name: str | None = None
    project_revision: int | None = None
    milestone_title: str | None = None
    verification_revision: int | None = None
    verification_status: str | None = None
    label_name: str | None = None
    label_revision: int | None = None
    document_title: str | None = None
    request_title: str | None = None
    request_status: str | None = None
    request_href: str | None = None


class CoordinatorSend(AgentRecord):
    view_context: CoordinatorViewSelection | None = None
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,100}$")
    text: str = Field(min_length=1, max_length=16000)
    task_context: CoordinatorTaskSelection | None = None


class CoordinatorTurn(AgentRecord):
    guidance_revision: int | None = None
    view_context: CoordinatorViewContext | None = None
    task_context: CoordinatorTaskContext | None = None
    id: str
    number: int = 0
    workspace_id: str = Field(validation_alias=AliasChoices("workspace_id", "project_id"))
    conversation_id: str
    text: str
    created_at: str
    status: Literal[
        "starting",
        "running",
        "stopping",
        "completed",
        "stopped",
        "failed",
        "interrupted",
        "uncertain",
    ] = "starting"
    settings: EffectiveAgent
    applied: EffectiveAgent | None = None
    launch: HarnessLaunch | None = None
    activity: RunActivityPage = Field(default_factory=RunActivityPage)
    notice: str = ""
    native_started: bool = False
    session: Literal["new", "resumed", "unavailable"] | None = None


class CoordinatorPage(AgentRecord):
    welcome: str = ""
    context: ContextUsage | None = None
    conversation: CoordinatorConversation | None = None
    items: list[CoordinatorTurn]
    next_before: int | None = None
    active: CoordinatorTurn | None = None
    session_recovery_turn_id: str | None = None
