"""Saved, independent local inspection copies. No execution or approval authority."""

from typing import Literal

from pydantic import Field

from flowfield.execution_models import Record


class InspectionSettings(Record):
    project_id: str
    revision: int = 1
    run_command: str = ""


class InspectionConfig(Record):
    expected_revision: int = Field(ge=1)
    run_command: str = Field(max_length=4000)


class InspectionPrepare(Record):
    # Preparation is always bound to a selected result.
    expected_revision: int = Field(ge=1)
    result_id: str = Field(min_length=1, max_length=200)
    new_copy: bool = False


class Inspection(Record):
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
    instructions_changed: bool = False
