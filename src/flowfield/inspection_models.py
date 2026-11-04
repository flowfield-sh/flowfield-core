"""Saved, independent local inspection copies. No execution or approval authority."""

from typing import Literal, Self

from pydantic import Field, model_validator

from flowfield.execution_models import Record
from flowfield.output_models import OutputFile, OutputSelection


class InspectionSettings(Record):
    project_id: str
    revision: int = 1
    run_command: str = ""
    no_preview_reason: str = ""
    auto_prepare: bool = False
    ready_command: str = ""
    url: str = ""
    outputs: list[OutputSelection] = Field(default_factory=list, max_length=32)


class InspectionConfig(Record):
    expected_revision: int = Field(ge=1)
    run_command: str = Field(max_length=4000)
    no_preview_reason: str = Field(default="", max_length=500)
    auto_prepare: bool = False
    ready_command: str = Field(default="", max_length=4000)
    url: str = Field(default="", max_length=2000, pattern=r"^(https?://[^\s]+)?$")
    outputs: list[OutputSelection] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def preview_choice(self) -> Self:
        if self.run_command.strip() and self.no_preview_reason.strip():
            raise ValueError("Choose a run command or explain why no runnable preview applies.")
        return self


class InspectionPrepare(Record):
    # Preparation is always bound to a selected result.
    expected_revision: int = Field(ge=1)
    result_id: str = Field(min_length=1, max_length=200)
    new_copy: bool = False


class Inspection(Record):
    setup_timeout_seconds: int = 120
    run_timeout_seconds: int = 60
    outputs: list[OutputSelection] = Field(default_factory=list)
    files: list[OutputFile] = Field(default_factory=list)
    resolved_url: str = ""
    artifact_problem: str | None = None
    workspace_id: str
    id: str
    project_id: str
    result_id: str | None = None
    task_key: str | None = None
    version: int | None = None
    target_branch: str
    commit: str
    created_at: str
    status: Literal["preparing", "ready", "failed"] = "preparing"
    workspace: str | None = None
    command: str = ""
    launcher: str | None = None
    runtime: Literal["local"] = "local"
    setup_commands: list[str] = Field(default_factory=list)
    run_command: str = ""
    problem: str | None = None
    # Observations, not saved evidence or instructions to change the original copy.
    locally_changed: bool = False
    source_changed: bool = False
    ready_command: str = ""
    url: str = ""
    runtime_state: Literal[
        "cold",
        "preparing",
        "ready",
        "starting",
        "running",
        "stopping",
        "stopped",
        "finished",
        "failed",
        "uncertain",
    ] = "cold"
    runtime_problem: str | None = None
    runtime_output: str = ""
    runtime_truncated: bool = False
    runtime_session: str | None = None
    runtime_prepared: bool = False
    runtime_generation: str | None = None
    runtime_log_truncated: bool = False


class InspectionRuntimeAction(Record):
    action: Literal["prepare", "run", "stop", "confirm-stopped"]
