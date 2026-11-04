"""Frozen result recipes and service-owned verification evidence."""

import hashlib
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flowfield.output_models import OutputSelection


class ResultRecipe(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_serialization_defaults_required=True,
    )

    setup_commands: list[Annotated[str, Field(min_length=1, max_length=4000)]] = Field(
        default_factory=list, max_length=10
    )
    setup_timeout_seconds: int = Field(default=120, ge=1, le=900)
    run_command: str = Field(default="", max_length=4000)
    ready_command: str = Field(default="", max_length=4000)
    run_timeout_seconds: int = Field(default=60, ge=1, le=900)
    url: str = Field(default="", max_length=2000, pattern=r"^(https?://[^\s]+)?$")
    no_preview_reason: str = Field(default="", max_length=500)
    outputs: list[OutputSelection] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.run_command.strip() and self.no_preview_reason.strip():
            raise ValueError("Choose a run command or explain why no runnable result applies.")
        if len({item.path for item in self.outputs}) != len(self.outputs):
            raise ValueError("Select each generated output only once.")
        return self

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class RuntimeStep(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    name: str
    passed: bool
    output: str = ""
    exit_code: int | None = None


class RuntimeVerification(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    candidate_commit: str
    recipe_digest: str
    workspace: str | None = None
    passed: bool = False
    cleanup_confirmed: bool = False
    problem: str | None = None
    steps: list[RuntimeStep] = Field(default_factory=list)
