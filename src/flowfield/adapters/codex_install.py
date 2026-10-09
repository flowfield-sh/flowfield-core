"""Explicit installation and native launch of the standalone Codex ACP bridge."""

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flowfield.adapters.bridge_install import (
    MAX_ARCHIVE,
    MAX_UNPACKED,
    BridgeBundle,
    target,
)
from flowfield.adapters.bridge_install import digest as digest
from flowfield.errors import ApplicationError

SPEC = json.loads(Path(__file__).with_name("codex_bridge.json").read_text())
VERSION: str = SPEC["version"]
EXECUTABLE = "flowfield-codex-acp"
FILES = {EXECUTABLE, "LICENSE", "THIRD_PARTY_NOTICES.txt"}


def _bundle() -> BridgeBundle:
    return BridgeBundle(
        "codex", "Codex", EXECUTABLE, SPEC, max_archive=MAX_ARCHIVE, max_unpacked=MAX_UNPACKED
    )


def manifest(path: Path) -> dict[str, Any]:
    return _bundle().manifest(path)


def verify(directory: Path) -> dict[str, Any]:
    return _bundle().verify(directory)


def installed(directory: Path) -> Path:
    return _bundle().installed(directory)


def install(directory: Path, bundle: Path, expected_sha256: str) -> Path:
    return _bundle().install(directory, bundle, expected_sha256)


def download_install(directory: Path) -> Path:
    return _bundle().download_install(directory)


def status(directory: Path, environment: Mapping[str, str]) -> dict[str, Any]:
    binary = installed(directory)
    codex = shutil.which(environment.get("CODEX_PATH") or "codex", path=environment.get("PATH", ""))
    return {
        "harness": "codex",
        "version": VERSION,
        "platform": target(),
        "executable": str(binary),
        "codex": codex,
        "codex_available": codex is not None,
        "message": "Managed Codex runtime installed."
        if codex
        else "Bridge installed. Install Codex on the service PATH before starting managed work.",
    }


def command(directory: Path, environment: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    """Resolve one immutable launch; native access policy remains harness-owned."""
    value = status(directory, environment)
    if not value["codex_available"]:
        raise ApplicationError("codex_missing", value["message"], 409)
    launch = dict(environment)
    launch["CODEX_PATH"] = str(Path(value["codex"]).resolve())
    # Bridge wire logging contains scoped MCP credentials. Native Codex settings
    # remain inherited; service-owned transport tokens must not enter debug files.
    launch.pop("APP_SERVER_LOGS", None)
    return [value["executable"]], launch
