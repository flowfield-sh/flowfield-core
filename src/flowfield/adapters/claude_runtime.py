"""Official SDK helper bundled with Flowfield; no user-side support installation."""

import json
import os
import platform
from pathlib import Path

from flowfield.errors import ApplicationError

SDK_VERSION = "0.3.293"
ADAPTER_VERSION = "claude-sdk-v1"


def executable() -> Path:
    machine = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(
        platform.machine()
    )
    system = platform.system().lower()
    if f"{system}-{machine}" not in {"darwin-arm64", "linux-arm64", "linux-x64"}:
        raise ApplicationError(
            "platform_unsupported", "Use an Apple Silicon Mac or Linux arm64/x64 host.", 409
        )
    root = Path(__file__).resolve().parents[1] / "_native"
    directory = root / "claude-sdk"
    if not directory.is_dir():
        directory = root / f"claude-sdk-{system}-{machine}"
    binary = directory / "claude-sdk"
    try:
        metadata = json.loads((directory / "version.json").read_text())
        if metadata != {
            "version": 1,
            "sdk": SDK_VERSION,
            "compiler": "1.3.11",
            "target": f"{system}-{machine}",
        }:
            raise ValueError("Wrong SDK runtime build")
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError("Missing SDK runtime")
        if not (directory / "THIRD_PARTY_NOTICES.txt").is_file():
            raise ValueError("Missing SDK license notices")
    except (OSError, ValueError) as error:
        raise ApplicationError(
            "claude_runtime_missing",
            "This Flowfield installation is incomplete. Reinstall Flowfield "
            "to restore Claude support.",
            409,
        ) from error
    return binary
