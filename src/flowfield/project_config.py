"""Portable project identity; task records remain in the application store."""

import json
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from flowfield.errors import ApplicationError

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1, le=1)
    project_id: Identifier
    name: Title


def read_config(root: Path) -> ProjectConfig | None:
    directory = root / ".flowfield"
    file = directory / "config.toml"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ApplicationError("config_conflict", f"{directory} must be a regular directory.", 409)
    if file.is_symlink():
        raise ApplicationError("config_conflict", f"Refusing symlinked project config: {file}", 409)
    if not file.exists():
        return None
    try:
        with file.open("rb") as stream:
            return ProjectConfig.model_validate(tomllib.load(stream))
    except (OSError, ValueError, ValidationError) as error:
        raise ApplicationError(
            "invalid_config", f"Cannot read {file}: {error}. Fix the config before continuing."
        ) from error


def write_config(root: Path, config: ProjectConfig) -> None:
    """Publish a complete config without replacing an existing file."""
    directory = root / ".flowfield"
    directory.mkdir(exist_ok=True)
    target = directory / "config.toml"
    # JSON string escaping is valid TOML for these basic Unicode strings.
    content = (
        f"version = 1\nproject_id = {json.dumps(config.project_id, ensure_ascii=False)}\n"
        f"name = {json.dumps(config.name, ensure_ascii=False)}\n"
    ).replace("\x7f", "\\u007f")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def discover_project(start: Path) -> str:
    """CLI convenience only; service requests still carry explicit project IDs."""
    root = start.resolve()
    for directory in (root, *root.parents):
        config = read_config(directory)
        if config is not None:
            return config.project_id
        if (directory / ".git").exists():
            break
    raise ApplicationError(
        "project_required",
        "No project config found. Adopt this repository in Flowfield or pass --project.",
    )
