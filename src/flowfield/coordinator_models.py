"""Durable conversation evidence; native sessions are not application identities."""

from typing import Literal

from pydantic import Field

from flowfield.agent_models import AgentRecord, EffectiveAgent
from flowfield.harness_models import HarnessLaunch
from flowfield.run_activity import ContextUsage, RunActivityPage


class CoordinatorConversation(AgentRecord):
    id: str
    number: int
    created_at: str


class CoordinatorTaskSelection(AgentRecord):
    task_id: str = Field(min_length=1, max_length=200)
    task_revision: int = Field(ge=1)
    result_id: str | None = Field(default=None, min_length=1, max_length=200)


class CoordinatorTaskContext(CoordinatorTaskSelection):
    key: str
    title: str


class CoordinatorSend(AgentRecord):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,100}$")
    text: str = Field(min_length=1, max_length=16000)
    task_context: CoordinatorTaskSelection | None = None


class CoordinatorTurn(AgentRecord):
    task_context: CoordinatorTaskContext | None = None
    id: str
    number: int = 0
    project_id: str
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
    context: ContextUsage | None = None
    conversation: CoordinatorConversation | None = None
    items: list[CoordinatorTurn]
    next_before: int | None = None
    active: CoordinatorTurn | None = None
    session_recovery_turn_id: str | None = None
