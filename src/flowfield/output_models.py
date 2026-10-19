"""Submitted files and immutable workspace output references."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OutputSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)

    @field_validator("path")
    @classmethod
    def relative_file(cls, value: str) -> str:
        if (
            value.startswith("/")
            or "\\" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("Choose a relative file path inside FLOWFIELD_OUTPUT_DIR.")
        return value


class OutputFile(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    id: str
    workspace_id: str
    project_id: str | None
    task_id: str
    task_key: str
    task_title: str

    run_id: str
    name: str
    description: str
    media_type: str
    size: int
    sha256: str
    created_at: str
    result_id: str | None = None
    result_version: int | None = None
    result_status: str | None = None


class FileInput(BaseModel):
    id: str
    kind: str
    name: str
    size: int
    sha256: str


class OutputPage(BaseModel):
    items: list[OutputFile]
    next_before: int | None = None


class OutputText(BaseModel):
    text: str
    supported: bool
    truncated: bool
