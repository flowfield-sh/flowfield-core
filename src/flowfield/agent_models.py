"""Shared agent preferences and immutable settings provenance."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from flowfield.harness_models import HarnessKind, HarnessRegistration


class AgentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class AgentChoice(AgentRecord):
    harness: HarnessKind = "codex"
    model: str = Field(min_length=1, max_length=200)
    effort: str | None = Field(default=None, min_length=1, max_length=40)
    mode: str | None = Field(default=None, min_length=1, max_length=200)
    fast: bool | None = None  # None preserves unknown historical/inherited configuration.


class EffectiveAgent(AgentRecord):
    choice: AgentChoice
    source: Literal["project", "override"]
    default_revision: int
    override_revision: int | None = None
    registration: HarnessRegistration | None = None


class AgentSettingsView(AgentRecord):
    revision: int = 1
    selection: AgentChoice | None = None
    effective: EffectiveAgent | None = None


class AgentSettingsEdit(AgentRecord):
    expected_revision: int = Field(ge=1)
    selection: AgentChoice | None = None


class AgentCommand(AgentRecord):
    name: str = Field(pattern=r"^\$?[a-zA-Z0-9_.-]+$", max_length=100)
    description: str = Field(max_length=1000)
    input_hint: str | None = Field(default=None, max_length=200)


AgentRole = Literal["worker", "coordinator"]
