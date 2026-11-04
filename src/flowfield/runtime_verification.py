"""Model-free recipe verification on a separate candidate copy, before independent review."""

import asyncio
import contextlib
import os
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.local_result import LocalResultEnvironment
from flowfield.adapters.result_environment import wait_ready
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.outputs import MAX_FILE_BYTES, MAX_SUBMISSION_BYTES, Outputs
from flowfield.runtime_models import ResultRecipe, RuntimeStep, RuntimeVerification


def require_cleanup(workspace: Workspace, workspace_id: str, task_id: str) -> None:
    """A prior failed/interrupted verification cannot leave a second runtime behind."""
    with workspace.connection() as db:
        identities = db.execute(
            "SELECT id FROM integrations WHERE workspace_id=? AND json_extract(data,'$.task_id')=?",
            (workspace_id, task_id),
        ).fetchall()
    root = workspace.directory / "result-verification" / "local-attempts"
    for (identity,) in identities:
        runtime = root / identity / "runtime"
        for receipt in runtime.glob("owner-*.json"):
            if not LocalResultEnvironment.clean(receipt):
                raise ApplicationError(
                    "runtime_cleanup_uncertain",
                    "Previous result verification cleanup is unconfirmed. "
                    "Inspect its owned processes and retained receipts before retrying.",
                    409,
                )
        LocalResultEnvironment.recover_temporary(runtime)


async def verify(
    directory: Path,
    repository: Path,
    candidate: str,
    recipe: ResultRecipe,
    cancelled: Callable[[], bool],
    identity: str,
) -> RuntimeVerification:
    evidence = RuntimeVerification(candidate_commit=candidate, recipe_digest=recipe.digest())
    copy = await asyncio.to_thread(
        LocalResultEnvironment.prepare_copy, directory, repository, identity, candidate
    )
    evidence.workspace = str(copy.checkout)
    output = ""

    def receive(block: str) -> None:
        nonlocal output
        output = (output + block)[-16000:]

    environment = LocalResultEnvironment(
        identity, copy.checkout, uuid4().hex, receive, cache_root=directory.parent / "setup-cache"
    )
    work = asyncio.current_task()

    async def watch() -> None:
        while True:
            if await asyncio.to_thread(cancelled):
                assert work
                work.cancel()
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch())
    try:
        for command in recipe.setup_commands:
            output = ""
            async with asyncio.timeout(recipe.setup_timeout_seconds):
                code = await environment.run(command)
            evidence.steps.append(
                RuntimeStep(name="Prepare", passed=code == 0, exit_code=code, output=output)
            )
            if code:
                raise RuntimeError(f"Result preparation failed (exit {code}).")
        for attempt in (1, 2):
            output = ""
            process = asyncio.create_task(environment.run(recipe.run_command))
            try:
                if recipe.ready_command:
                    await wait_ready(
                        environment, process, recipe.ready_command, recipe.run_timeout_seconds
                    )
                    evidence.steps.append(
                        RuntimeStep(name=f"Run {attempt}: ready", passed=True, output=output)
                    )
                else:
                    async with asyncio.timeout(recipe.run_timeout_seconds):
                        code = await asyncio.shield(process)
                    evidence.steps.append(
                        RuntimeStep(
                            name=f"Run {attempt}: completed",
                            passed=code == 0,
                            exit_code=code,
                            output=output,
                        )
                    )
                    if code:
                        raise RuntimeError(f"Result run {attempt} failed (exit {code}).")
            finally:
                if not process.done():
                    process.cancel()
                await asyncio.gather(process, return_exceptions=True)
            clean = all(environment.clean(path) for path in environment.receipts)
            evidence.steps.append(RuntimeStep(name=f"Run {attempt}: cleanup", passed=clean))
            if not clean:
                raise RuntimeError("Result process cleanup was not confirmed.")
            total = 0
            for selected in recipe.outputs:
                descriptor = Outputs._source(environment.runtime / "output", selected.path)
                try:
                    size = os.fstat(descriptor).st_size
                    total += size
                    if size > MAX_FILE_BYTES or total > MAX_SUBMISSION_BYTES:
                        raise ValueError(
                            "Generated files must fit 128 MiB per file and 512 MiB per run."
                        )
                finally:
                    os.close(descriptor)
            if not await asyncio.to_thread(gitops.unchanged, copy, candidate):
                raise RuntimeError("Running the result changed candidate source files.")
        evidence.passed = True
    except asyncio.CancelledError:
        evidence.problem = "Result verification stopped; evidence and files were retained."
    except TimeoutError:
        evidence.problem = (
            "Result verification timed out. Services need a readiness command; "
            "batch commands must finish."
        )
    except Exception as error:
        evidence.problem = str(error)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        evidence.cleanup_confirmed = await environment.close()
        if not evidence.cleanup_confirmed:
            evidence.passed = False
            evidence.problem = (
                "Result verification cleanup is unconfirmed. "
                "Inspect its owned processes before retrying."
            )
        if not evidence.passed:
            evidence.steps.append(
                RuntimeStep(name="Verification failed", passed=False, output=output)
            )
    return evidence
