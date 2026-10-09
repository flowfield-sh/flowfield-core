"""Harness-neutral records for managed work; adapter metadata is stored separately."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flowfield.agent_models import AgentChoice, EffectiveAgent
from flowfield.harness_models import HarnessLaunch

ACTIVE = ("preparing", "running", "stopping", "uncertain")
RunStatus = Literal[
    "preparing",
    "running",
    "stopping",
    "uncertain",
    "stopped",
    "failed",
    "waiting_for_input",
    "in_review",
    "changes_requested",
    "accepted",
]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class WorkerSettings(Record):
    project_id: str
    revision: int = 1
    selection: AgentChoice | None = None
    max_parallel: int = 1
    enabled: bool = False
    problem: str | None = None


class WorkerOccupancy(Record):
    active: int
    uncertain: int


class SettingsEdit(Record):
    expected_revision: int = Field(ge=1)
    selection: AgentChoice
    max_parallel: int = Field(default=1, ge=1, le=16)


class QueueEdit(Record):
    expected_revision: int = Field(ge=1)
    enabled: bool


class RunAction(Record):
    expected_revision: int = Field(ge=1)
    note: str = Field(default="", max_length=8000)
    author: str = Field(default="human", min_length=1, max_length=200)


class WorkerResult(Record):
    summary: str = Field(min_length=1, max_length=8000)
    checks: str = Field(min_length=1, max_length=8000)
    limitations: str = Field(default="", max_length=8000)
    outcome: Literal["complete", "partial"] = "complete"
    remaining_work: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if self.outcome == "partial" and not self.remaining_work.strip():
            raise ValueError("Describe the agreed work that remains unfinished.")
        if self.outcome == "complete" and self.remaining_work.strip():
            raise ValueError("Unfinished required work must be reported as partial.")
        return self


class WorkerSubmission(WorkerResult):
    # The model must choose explicitly; stored/model-free fixtures retain convenient defaults.
    outcome: Literal["complete", "partial"]


class NativeMode(Record):
    id: str
    name: str
    description: str = ""


class ModelOption(Record):
    id: str
    name: str
    efforts: list[str]
    modes: list[NativeMode] = Field(default_factory=list)
    fast: bool = False
    fast_description: str = ""


class Usage(Record):
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None
    complete: bool = False


class CheckResult(Record):
    command: str
    exit_code: int
    output: str
    truncated: bool = False


class Correction(Record):
    result_id: str
    source_commit: str
    target_commit: str
    base_commit: str
    details: str
    number: int


class Run(Record):
    model_config = ConfigDict(extra="ignore")
    agent_settings: EffectiveAgent | None = None
    applied_agent: AgentChoice | None = None
    # Nonsecret observed native adapter locations; external credentials/files are not frozen.
    harness_launch: HarnessLaunch | None = None
    id: str
    project_id: str
    task_id: str
    task_key: str
    purpose: Literal["work"] = "work"
    reply_id: str | None = None
    revision: int = 1
    status: RunStatus = "preparing"
    environment_id: str
    predecessor_id: str | None = None
    question_id: str | None = None
    input_question_id: str | None = None
    input_base_commit: str | None = None
    input_checkpoint: str | None = None
    correction: Correction | None = None
    next_correction: Correction | None = None
    runtime: Literal["local"] = "local"
    setup_commands: list[str] = Field(default_factory=list)
    setup_timeout_seconds: int = 120
    setup_checks: list[CheckResult] = Field(default_factory=list)
    model: str
    effort: str | None = None
    agreement_revision: int
    base_commit: str
    completion: Literal["code", "report"] = "code"
    target_branch: str | None = None
    result_commit: str | None = None
    result: WorkerResult | None = None
    feedback: str = ""
    problem: str | None = None
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    accepted_at: str | None = None
    accepted_by: str | None = None
    code_available: bool = False
    usage: Usage = Field(default_factory=Usage)
    excluded_files: list[str] = Field(default_factory=list)


class RunPage(Record):
    items: list[Run]
    next_before: int | None = None


class RunLocation(Record):
    workspace: str | None = None
    diff_command: str | None = None
    try_command: str | None = None


SCHEMA = """
CREATE TABLE worker_settings (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), data TEXT NOT NULL
);
CREATE TABLE runs (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL,
    status TEXT NOT NULL, data TEXT NOT NULL, assignment TEXT NOT NULL,
    FOREIGN KEY(project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE INDEX runs_project ON runs(project_id, task_id, number);
CREATE UNIQUE INDEX run_owner ON runs(project_id, task_id)
WHERE status IN ('preparing', 'running', 'stopping', 'uncertain');
CREATE TABLE execution_local (
    run_id TEXT PRIMARY KEY REFERENCES runs(id), data TEXT NOT NULL
);
"""
