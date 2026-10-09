"""Concrete native path precedence; detection launches nothing and reads no credentials."""

import asyncio
import json
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from flowfield.adapters.local_process import LocalProcess
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessKind, HarnessLaunch, HarnessRegistration, HarnessStatus


class NativePaths(NamedTuple):
    command: str
    executable_variable: str
    config_variable: str
    config_directory: str
    adapter_version: str
    auth_command: tuple[str, ...]


NATIVE_PATHS: dict[HarnessKind, NativePaths] = {
    "codex": NativePaths(
        "codex", "CODEX_PATH", "CODEX_HOME", ".codex", "codex-app-server-v1", ("login", "status")
    ),
    "claude-code": NativePaths(
        "claude",
        "CLAUDE_CODE_EXECUTABLE",
        "CLAUDE_CONFIG_DIR",
        ".claude",
        "claude-sdk-v1",
        ("auth", "status", "--json"),
    ),
}


def same_session_location(old: HarnessLaunch, new: HarnessLaunch) -> bool:
    # A saved revision/source or immutable installation generation can change
    # without redirecting native history. A changed adapter version needs revalidation.
    return (old.harness, old.native_executable, old.config_directory, old.adapter_version) == (
        new.harness,
        new.native_executable,
        new.config_directory,
        new.adapter_version,
    )


def resolve(registration: HarnessRegistration, environment: Mapping[str, str]) -> HarnessLaunch:
    paths = NATIVE_PATHS[registration.harness]
    configured = registration.executable or environment.get(paths.executable_variable)
    native = shutil.which(configured or paths.command, path=environment.get("PATH", ""))
    config = registration.config_directory or environment.get(paths.config_variable)
    location = (
        Path(config)
        if config
        else Path(environment.get("HOME") or Path.home()) / paths.config_directory
    )
    return HarnessLaunch(
        harness=registration.harness,
        registration_revision=registration.revision,
        native_executable=str(Path(native).resolve()) if native else None,
        executable_source="registration"
        if registration.executable
        else "environment"
        if configured
        else "path",
        config_directory=str(location.resolve()) if location.is_absolute() else str(location),
        adapter_version=paths.adapter_version,
        config_source="registration"
        if registration.config_directory
        else "environment"
        if config
        else "default",
    )


def launch_environment(launch: HarnessLaunch, environment: Mapping[str, str]) -> dict[str, str]:
    if not launch.native_executable or not os.access(launch.native_executable, os.X_OK):
        raise ApplicationError(
            "harness_missing", "Install the native harness or correct its executable path.", 409
        )
    location = Path(launch.config_directory)
    if not location.is_absolute():
        raise ApplicationError(
            "harness_config_path", "Native configuration must use an absolute host directory.", 409
        )
    if (
        location.exists()
        and not location.is_dir()
        or (launch.config_source != "default" and not location.is_dir())
    ):
        raise ApplicationError(
            "harness_config_missing",
            "Native configuration location must be an existing directory.",
            409,
        )
    result = dict(environment)
    paths = NATIVE_PATHS[launch.harness]
    result[paths.executable_variable] = launch.native_executable
    result[paths.config_variable] = launch.config_directory
    return result


def status(
    directory: Path, registration: HarnessRegistration, environment: Mapping[str, str]
) -> HarnessStatus:
    launch = resolve(registration, environment)
    problems = []
    if not launch.native_executable:
        problems.append("native_missing")
    config = Path(launch.config_directory)
    available = config.is_absolute() and (
        config.is_dir() or launch.config_source == "default" and not config.exists()
    )
    if not available:
        problems.append("config_directory_missing")
    return HarnessStatus(
        registration=registration,
        launch=launch,
        native_installed=launch.native_executable is not None,
        config_available=available,
        selectable=not problems,
        problems=problems,
    )


async def _native_command(
    command: list[str], cwd: Path, environment: Mapping[str, str]
) -> tuple[int, bytes, bytes]:
    """Bound time and retained bytes; never surface raw native output."""
    owner = await LocalProcess.start(command, cwd=cwd, env=environment)
    assert owner.process.stdin
    owner.process.stdin.close()  # Status commands have no interactive input or login flow.
    exceeded = asyncio.Event()

    async def drain(stream: asyncio.StreamReader | None, retain: bool) -> bytes:
        assert stream
        output = bytearray()
        size = 0
        while chunk := await stream.read(4096):
            size += len(chunk)
            if size > 16384:
                exceeded.set()
            elif retain:
                output.extend(chunk)
        return bytes(output)

    readers = [
        asyncio.create_task(drain(owner.process.stdout, True)),
        asyncio.create_task(drain(owner.process.stderr, True)),
    ]
    exit_job = asyncio.create_task(owner.process.wait())
    limit_job = asyncio.create_task(exceeded.wait())
    try:
        async with asyncio.timeout(10):
            await asyncio.wait((exit_job, limit_job), return_when=asyncio.FIRST_COMPLETED)
            if exceeded.is_set():
                raise ValueError("Native check output exceeded its bound")
            output, error_output = await asyncio.gather(*readers)
            code = await exit_job
            if exceeded.is_set():
                raise ValueError("Native check output exceeded its bound")
        return code, output, error_output
    finally:
        await owner.close(timeout=1)
        # Keep consuming pipes during termination; abandoning a paused reader can
        # retain its transport after the event loop closes.
        pending = [task for task in readers if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=1)
        tasks = [*readers, exit_job, limit_job]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def check(
    directory: Path, registration: HarnessRegistration, environment: Mapping[str, str]
) -> HarnessStatus:
    """Explicit native version/login-status only; never a model turn or login flow."""
    result = status(directory, registration, environment)
    result.checked = True
    if not result.native_installed or not result.config_available:
        return result
    launch = result.launch
    assert launch.native_executable
    try:
        env = launch_environment(launch, environment)
        env.pop("APP_SERVER_LOGS", None)
        code, output, _ = await _native_command(
            [launch.native_executable, "--version"], directory, env
        )
        version = re.fullmatch(
            rb"(?:codex-cli )?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)(?: \(Claude Code\))?\s*",
            output,
        )
        if code != 0 or version is None:
            raise ValueError("Unrecognized native version")
        result.native_version = version[1].decode("ascii")
        args = NATIVE_PATHS[registration.harness].auth_command
        code, output, error_output = await _native_command(
            [launch.native_executable, *args], directory, env
        )
        if registration.harness == "claude-code":
            value = json.loads(output)
            if isinstance(value, dict) and type(value.get("loggedIn")) is bool:
                if code == 0 and value["loggedIn"]:
                    result.authentication = "authenticated"
                elif code in (0, 1) and not value["loggedIn"]:
                    result.authentication = "signed-out"
        else:
            lines = (output or error_output).splitlines()
            if code == 0 and any(line.startswith(b"Logged in") for line in lines):
                result.authentication = "authenticated"
            elif code == 1 and b"Not logged in" in lines:
                result.authentication = "signed-out"
        if result.authentication == "unknown":
            result.problems.append("authentication_unverified")
        elif result.authentication == "signed-out":
            result.problems.append("native_login_required")
        result.selectable = result.selectable and result.authentication != "signed-out"
    except (OSError, ValueError, RuntimeError, TimeoutError):
        result.problems.append("native_check_failed")
    return result


class HarnessChecks:
    """At most one live check per concrete kind; request cancellation retains its owner."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.jobs: dict[str, tuple[str, asyncio.Task[HarnessStatus]]] = {}
        self.closing = False

    async def run(
        self, registration: HarnessRegistration, environment: Mapping[str, str]
    ) -> HarnessStatus:
        if self.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        signature = resolve(registration, environment).model_dump_json()
        old = self.jobs.get(registration.harness)
        if old and not old[1].done():
            if old[0] != signature:
                raise ApplicationError(
                    "harness_check_busy", "Host settings changed; check again shortly.", 409
                )
            return await asyncio.shield(old[1])
        job = asyncio.create_task(
            check(self.directory, registration.model_copy(deep=True), dict(environment))
        )
        self.jobs[registration.harness] = (signature, job)
        return await asyncio.shield(job)

    async def close(self) -> None:
        self.closing = True
        jobs = [job for _, job in self.jobs.values()]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
