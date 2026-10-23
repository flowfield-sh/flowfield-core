"""Explicit project commands on Local, with bounded output and owned process cleanup.

These commands are trusted project configuration, not harness tool calls. Process
groups cover ordinary children; projects must not daemonize setup or check commands.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic

from flowfield.adapters.local_execution import LocalAttempt
from flowfield.adapters.local_process import LocalLaunchCancelled, LocalProcess
from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult
from flowfield.integration_models import CommandProgress


async def run_checks(
    environment: LocalAttempt,
    commands: list[str],
    timeout: int,
    on_process: Callable[[int | None], None] | None = None,
    *,
    on_progress: Callable[[CommandProgress, list[CheckResult]], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[CheckResult]:
    reports: list[CheckResult] = []
    for index, command in enumerate(commands, 1):
        report = await _run_check(
            environment,
            command,
            index,
            len(commands),
            timeout,
            reports,
            on_process,
            on_progress,
            cancelled,
        )
        reports.append(report)
        if report.exit_code:
            break
    return reports


async def _run_check(
    environment: LocalAttempt,
    command: str,
    index: int,
    total: int,
    timeout: int,
    reports: list[CheckResult],
    on_process: Callable[[int | None], None] | None,
    on_progress: Callable[[CommandProgress, list[CheckResult]], None] | None,
    cancelled: Callable[[], bool] | None,
) -> CheckResult:
    if cancelled and cancelled():
        return CheckResult(command=command, exit_code=130, output="")
    owner = None
    readers: list[asyncio.Task[None]] = []
    output = bytearray()
    truncated = False
    recent = bytearray()
    last_output: str | None = None
    started_at = datetime.now(UTC).isoformat()
    started = monotonic()

    def publish() -> None:
        if on_progress:
            on_progress(
                CommandProgress(
                    command=command,
                    index=index,
                    total=total,
                    started_at=started_at,
                    elapsed_seconds=monotonic() - started,
                    timeout_seconds=timeout,
                    output=recent.decode(errors="replace"),
                    truncated=truncated,
                    last_output_at=last_output,
                ),
                list(reports),
            )

    async def drain(stream: asyncio.StreamReader) -> None:
        nonlocal truncated, last_output
        while block := await stream.read(8192):
            remaining = 12000 - len(output)
            output.extend(block[:remaining])
            truncated |= len(block) > remaining
            recent.extend(block)
            del recent[:-12000]
            last_output = datetime.now(UTC).isoformat()

    try:
        owner = await LocalProcess.start(
            ["/bin/sh", "-c", command],
            cwd=environment.checkout,
            env=environment.launch_environment(),
        )
        process = owner.process
        if on_process:
            on_process(process.pid)
        assert process.stdin and process.stdout and process.stderr
        process.stdin.close()
        readers = [asyncio.create_task(drain(s)) for s in (process.stdout, process.stderr)]
        try:
            async with asyncio.timeout(timeout):
                if on_progress or cancelled:
                    while process.returncode is None:
                        publish()
                        if cancelled and cancelled():
                            code = 130
                            break
                        await asyncio.sleep(0.25)
                    else:
                        code = process.returncode
                else:
                    code = await process.wait()
        except TimeoutError:
            code = 124
    except LocalLaunchCancelled as error:
        owner = error.owner
        raise
    finally:
        if owner:
            confirmed = await owner.close()
            for reader in readers:
                try:
                    async with asyncio.timeout(1):
                        await asyncio.shield(reader)
                except TimeoutError:
                    reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if not confirmed:
                raise ApplicationError(
                    "command_cleanup_uncertain",
                    "Project command cleanup could not be confirmed. Inspect the retained "
                    "processes before retrying.",
                    409,
                )
            if on_process:
                on_process(None)
    return CheckResult(
        command=command, exit_code=code, output=output.decode(errors="replace"), truncated=truncated
    )


def command_problem(setup: list[CheckResult], checks: list[CheckResult]) -> str:
    """Name the observed failure and give bounded, evidence-based next steps."""
    phase, failed = next(
        (phase, check)
        for phase, reports in (("Setup", setup), ("Validation", checks))
        for check in reports
        if check.exit_code
    )
    output = failed.output.lower()
    detail = f"{phase} command failed (exit {failed.exit_code}): {failed.command[:200]}. "
    if "command not found" in output or failed.exit_code == 127:
        return detail + (
            "Install the tool on the service host or add reproducible project dependency "
            "setup. Check the PATH used to start Flowfield, then validate again."
        )
    if "library not loaded" in output or "error while loading shared libraries" in output:
        return detail + "Check the installed tool's library dependencies on the service host."
    if "permission denied" in output or "operation not permitted" in output:
        return detail + "Check the service user's access to the path named in the output."
    if any(
        marker in output for marker in ("connection refused", "econnrefused", "could not connect")
    ):
        return detail + (
            "A required service was unreachable. Ask the Coordinator to confirm the endpoint "
            "and project-owned startup/readiness command; validate again before retrying."
        )
    if failed.exit_code == 124:
        return detail + (
            "The command exceeded its time limit. Check readiness and use a bounded command "
            "that exits; setup commands cannot keep a background service running for workers."
        )
    return detail + (
        "Review the output with the Coordinator, correct the project setup or command, "
        "and validate again before retrying."
    )
