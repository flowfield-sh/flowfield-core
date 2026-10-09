"""Host configuration and nonsecret launch provenance for concrete harnesses."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HarnessKind = Literal["codex", "claude-code"]


class HarnessRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class HarnessConfiguration(HarnessRecord):
    executable: str | None = Field(default=None, min_length=1, max_length=4096)
    config_directory: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("executable", "config_directory")
    @classmethod
    def host_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            path = Path(value).expanduser()
        except RuntimeError:
            raise ValueError("Use an absolute service-host path (or ~/path).") from None
        if "\x00" in value or not path.is_absolute():
            raise ValueError("Use an absolute service-host path (or ~/path).")
        return str(path)


class HarnessRegistration(HarnessConfiguration):
    harness: HarnessKind
    revision: int = Field(default=1, ge=1)


class HarnessEdit(HarnessConfiguration):
    expected_revision: int = Field(ge=1)


class HarnessLaunch(HarnessRecord):
    harness: HarnessKind
    registration_revision: int = Field(ge=1)
    native_executable: str | None
    executable_source: Literal["registration", "environment", "path"]
    config_directory: str
    config_source: Literal["registration", "environment", "default"]
    bridge_executable: str | None = None
    bridge_version: str | None = None


class CatalogOwnership(HarnessRecord):
    id: str
    harness: HarnessKind
    project_id: str | None
    status: Literal["running", "uncertain"]
    started_at: str
    launch: HarnessLaunch
    operation: Literal["models", "commands"] = "models"


class CatalogConfirmation(HarnessRecord):
    id: str = Field(min_length=1, max_length=100)


class HarnessStatus(HarnessRecord):
    registration: HarnessRegistration
    launch: HarnessLaunch
    native_installed: bool
    config_available: bool
    bridge_installed: bool
    selectable: bool
    authentication: Literal["unknown", "authenticated", "signed-out"] = "unknown"
    model_access: Literal["unverified"] = "unverified"
    native_version: str | None = None
    checked: bool = False
    problems: list[str] = Field(default_factory=list)
    catalog_ownership: CatalogOwnership | None = None
    installing: bool = False
