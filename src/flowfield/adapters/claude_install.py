"""Explicit standalone bridge installation; native Claude Code remains user-owned."""

import json
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
from flowfield.adapters.harness_host import launch_environment, resolve
from flowfield.harness_models import HarnessRegistration

SPEC = json.loads(Path(__file__).with_name("claude_bridge.json").read_text())
VERSION: str = SPEC["version"]
EXECUTABLE = "flowfield-claude-acp"
FILES = {EXECUTABLE, "LICENSE", "THIRD_PARTY_NOTICES.txt"}


def _bundle() -> BridgeBundle:
    return BridgeBundle(
        "claude-code",
        "Claude Code",
        EXECUTABLE,
        SPEC,
        max_archive=MAX_ARCHIVE,
        max_unpacked=MAX_UNPACKED,
    )


def installed(directory: Path) -> Path:
    return _bundle().installed(directory)


def install(directory: Path, bundle: Path, expected_sha256: str) -> Path:
    return _bundle().install(directory, bundle, expected_sha256)


def download_install(directory: Path) -> Path:
    return _bundle().download_install(directory)


def status(
    directory: Path,
    environment: Mapping[str, str],
    *,
    registration: HarnessRegistration | None = None,
) -> dict[str, Any]:
    binary = installed(directory)
    native = resolve(
        registration or HarnessRegistration(harness="claude-code", revision=1), environment
    )
    return {
        "harness": "claude-code",
        "version": VERSION,
        "platform": target(),
        "executable": str(binary),
        "claude": native.native_executable,
        "claude_available": native.native_executable is not None,
        "message": "Managed Claude Code runtime installed."
        if native.native_executable
        else "Bridge installed. Install Claude Code on the service PATH "
        "or configure its executable.",
    }


def command(
    directory: Path,
    environment: Mapping[str, str],
    *,
    registration: HarnessRegistration,
) -> tuple[list[str], dict[str, str]]:
    binary = installed(directory)
    launch = launch_environment(resolve(registration, environment), environment)
    launch.pop("CLAUDE_AGENT_LOGS", None)
    launch.pop("CLAUDE_AGENT_ACP_EXPERIMENTAL_V2", None)
    return [str(binary)], launch
