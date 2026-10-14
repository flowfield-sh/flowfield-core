"""Explicit project commands on Local, with bounded output and owned process cleanup.

These commands are trusted project configuration, not harness tool calls. Process
groups cover ordinary children; projects must not daemonize setup or check commands.
"""

import asyncio
from collections.abc import Callable

from flowfield.adapters.local_execution import LocalAttempt
from flowfield.adapters.local_process import LocalLaunchCancelled, LocalProcess
from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult


async def run_checks(
    environment: LocalAttempt,
    commands: list[str],
    timeout: int,
    on_process: Callable[[int | None], None] | None = None,
) -> list[CheckResult]:
    reports = []
    for command in commands:
        owner = None
        readers: list[asyncio.Task[None]] = []
        output = bytearray()
        truncated = False

        async def drain(stream: asyncio.StreamReader, output: bytearray = output) -> None:
            nonlocal truncated
            while block := await stream.read(8192):
                remaining = 12000 - len(output)
                output.extend(block[:remaining])
                truncated |= len(block) > remaining

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
        reports.append(
            CheckResult(
                command=command,
                exit_code=code,
                output=output.decode(errors="replace"),
                truncated=truncated,
            )
        )
        if code:
            break
    return reports


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
