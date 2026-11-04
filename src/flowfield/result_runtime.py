"""Service-owned Local result environments; independent of Worker and approval lifetimes."""

import asyncio
import contextlib
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from uuid import uuid4

from flowfield.adapters.local_result import LocalResultEnvironment
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.inspection import Inspections
from flowfield.inspection_models import Inspection, InspectionPrepare
from flowfield.output_models import OutputFile
from flowfield.results import Results


class ResultRuntimes:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.copies = Inspections(workspace)
        self.session = uuid4().hex
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.values: dict[str, Inspection] = {}
        self.seen: set[str] = set()
        self.closing = False

    def _rows(self) -> list[Inspection]:
        with self.workspace.connection() as db:
            return [
                Inspection.model_validate_json(row[0])
                for row in db.execute("SELECT data FROM inspections")
            ]

    def recover(self) -> None:
        # Existing results never auto-start because Flowfield restarted.
        with self.workspace.connection() as db:
            self.seen = {
                row[0]
                for row in db.execute(
                    "SELECT id FROM result_versions WHERE status IN ('ready','delivered')"
                )
            }
        for value in self._rows():
            if value.workspace:
                LocalResultEnvironment.recover_temporary(self._runtime(value))
            previous = value.runtime_state
            was_prepared = value.runtime_prepared
            value.runtime_prepared = False
            if value.runtime_state in ("preparing", "starting", "running", "stopping"):
                receipts = self._receipts(value) if value.workspace else []
                retained = list(receipts)
                clean = all(self._clean(path) for path in retained)
                value.runtime_state = "cold" if clean else "uncertain"
                value.runtime_problem = (
                    None
                    if clean
                    else (
                        "Runtime cleanup was interrupted. Confirm its owned processes have stopped "
                        "before preparing again. Files and logs were kept."
                    )
                )
            elif value.runtime_state == "ready":
                value.runtime_state = "cold"
            if previous != value.runtime_state or was_prepared:
                self.copies._save(value)
            if value.runtime_state == "uncertain":
                self.values[value.id] = value

    _clean = staticmethod(LocalResultEnvironment.clean)

    @staticmethod
    def _runtime(value: Inspection) -> Path:
        assert value.workspace
        return Path(value.workspace).parent / "runtime"

    def _receipts(self, value: Inspection) -> list[Path]:
        return list(self._runtime(value).glob(f"owner-{value.runtime_generation}-*.json"))

    def _lock(self, workspace_id: str, task_id: str) -> asyncio.Lock:
        return self.locks.setdefault((workspace_id, task_id), asyncio.Lock())

    def _eligible(self, workspace_id: str, result_id: str, *, allow_delivery: bool = False) -> None:
        result = Results(self.workspace).get(workspace_id, result_id)
        page = Results(self.workspace).page(workspace_id, result.task_id, limit=1)
        task = self.workspace.task(workspace_id, result.task_id)
        if (
            self.closing
            or page.current_id != result_id
            or page.current_run_id != result.run_id
            or result.status
            not in (
                ("ready", "delivering", "delivered") if allow_delivery else ("ready", "delivered")
            )
            or task.archived
            or task.status not in ("in_review", "done")
        ):
            raise ApplicationError(
                "runtime_unavailable", "Only the current ready result can run.", 409
            )

    async def prepare(self, workspace_id: str, request: InspectionPrepare) -> Inspection:
        result = Results(self.workspace).get(workspace_id, request.result_id)
        async with self._lock(workspace_id, result.task_id):
            self._eligible(workspace_id, result.id)
            self.workspace._current(result.revision, request.expected_revision)
            previous = self.copies.latest(workspace_id, result.id)
            if previous:
                self._reconcile_cleanup(previous)
            if previous and previous.runtime_state == "uncertain":
                raise ApplicationError(
                    "runtime_cleanup_uncertain",
                    previous.runtime_problem or "Confirm cleanup first.",
                    409,
                )
            if previous and previous.id in self.jobs and not request.new_copy:
                return self.copies.get(workspace_id, previous.id)
            await self._stop_task(workspace_id, result.task_id)
            # This thread must finish before releasing the task lock on cancellation.
            copying = asyncio.create_task(
                asyncio.to_thread(self.copies.prepare, workspace_id, request)
            )
            try:
                value = await asyncio.shield(copying)
            except asyncio.CancelledError:
                await copying
                raise
            self._eligible(workspace_id, result.id)
            if value.problem or value.status != "ready" or not value.run_command:
                raise ApplicationError(
                    "runtime_unavailable", value.problem or "Save a run command first.", 409
                )
            if value.locally_changed:
                raise ApplicationError(
                    "inspection_changed",
                    "This copy has local edits. Prepare another copy; its files will be kept.",
                    409,
                )
            value.runtime_session = self.session
            value.runtime_prepared = False
            value.runtime_generation = uuid4().hex
            value.runtime_state = "preparing"
            value.runtime_problem = None
            value.runtime_output = ""
            value.runtime_truncated = False
            value.runtime_log_truncated = False
            self.values[value.id] = value
            self.copies._save(value)
            self._job(value, self._prepare(value))
            return value

    def _job(self, value: Inspection, work: Coroutine[Any, Any, None]) -> None:
        job = asyncio.create_task(work)
        self.jobs[value.id] = job
        job.add_done_callback(
            lambda done: self.jobs.pop(value.id, None) if self.jobs.get(value.id) is done else None
        )

    async def run(self, workspace_id: str, identity: str) -> Inspection:
        value = self.copies.get(workspace_id, identity)
        assert value.result_id
        result = Results(self.workspace).get(workspace_id, value.result_id)
        async with self._lock(workspace_id, result.task_id):
            self._eligible(workspace_id, result.id)
            value = self.copies.get(workspace_id, identity)
            if value.runtime_state in ("starting", "running") and identity in self.jobs:
                return value
            if (
                value.runtime_state not in ("ready", "stopped", "finished")
                or not value.runtime_prepared
                or value.runtime_session != self.session
            ):
                raise ApplicationError(
                    "runtime_not_prepared", "Prepare this result before running it.", 409
                )
            if value.locally_changed or value.problem:
                raise ApplicationError(
                    "inspection_changed",
                    "The copy changed. Prepare the result again.",
                    409,
                )
            await self._stop_task(workspace_id, result.task_id, except_id=identity)
            self._eligible(workspace_id, result.id)
            value.runtime_state = "starting"
            value.files = []
            value.runtime_generation = uuid4().hex
            value.runtime_problem = None
            value.runtime_output = ""
            value.runtime_truncated = False
            value.runtime_log_truncated = False
            self.values[identity] = value
            self.copies._save(value)
            self._job(value, self._run(value))
            return value

    async def stop(self, workspace_id: str, identity: str) -> Inspection:
        value = self.copies.get(workspace_id, identity)
        assert value.result_id
        result = Results(self.workspace).get(workspace_id, value.result_id)
        async with self._lock(workspace_id, result.task_id):
            await self._stop(identity, value)
            return self.copies.get(workspace_id, identity)

    async def stop_task(self, workspace_id: str, task_id: str) -> None:
        async with self._lock(workspace_id, task_id):
            await self._stop_task(workspace_id, task_id)

    async def confirm_stopped(self, workspace_id: str, identity: str) -> Inspection:
        value = self.copies.get(workspace_id, identity)
        assert value.result_id
        result = Results(self.workspace).get(workspace_id, value.result_id)
        async with self._lock(workspace_id, result.task_id):
            value = self.copies.get(workspace_id, identity)
            if identity in self.jobs or value.runtime_state != "uncertain":
                raise ApplicationError(
                    "runtime_active",
                    "Only an uncertain, inactive runtime can be confirmed stopped.",
                    409,
                )
            value.runtime_state = "stopped"
            value.runtime_prepared = False
            value.runtime_problem = "Process cleanup confirmed by the user. Prepare again to run."
            self.copies._save(value)
            self.values[identity] = value
            return value

    async def _stop_task(self, workspace_id: str, task_id: str, *, except_id: str = "") -> None:
        with self.workspace.connection() as db:
            rows = db.execute(
                "SELECT i.data FROM inspections i JOIN result_versions r ON r.id=i.result_id "
                "WHERE i.workspace_id=? AND r.task_id=?",
                (workspace_id, task_id),
            ).fetchall()
        for row in rows:
            value = Inspection.model_validate_json(row[0])
            if value.id != except_id and value.runtime_state in (
                "preparing",
                "ready",
                "starting",
                "running",
                "stopping",
                "uncertain",
            ):
                await self._stop(value.id, value)

    async def _stop(self, identity: str, value: Inspection) -> None:
        job = self.jobs.get(identity)
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            value = self.values[identity]
        self._reconcile_cleanup(value)
        if value.runtime_state == "uncertain":
            raise ApplicationError(
                "runtime_cleanup_uncertain",
                value.runtime_problem or "Runtime cleanup is unconfirmed.",
                409,
            )
        value.runtime_state = "stopped"
        if self.closing:
            value.runtime_prepared = False
        self.values[identity] = value
        self.copies._save(value)

    def _reconcile_cleanup(self, value: Inspection) -> None:
        if value.runtime_state == "uncertain" and value.runtime_generation:
            receipts = self._receipts(value)
            if receipts and all(self._clean(path) for path in receipts):
                value.runtime_state = "stopped"
                value.runtime_problem = None
                self.copies._save(value)

    async def _prepare(self, value: Inspection) -> None:
        await self._operate(value, preparing=True)

    async def _run(self, value: Inspection) -> None:
        await self._operate(value, preparing=False)

    async def _operate(self, value: Inspection, *, preparing: bool) -> None:
        assert value.workspace and value.runtime_generation

        def output(block: str) -> None:
            combined = (value.runtime_output + block).encode()
            value.runtime_truncated |= len(combined) > 16000
            value.runtime_output = combined[-16000:].decode(errors="ignore")

        environment = LocalResultEnvironment(
            value.id, Path(value.workspace), value.runtime_generation, output
        )
        value.resolved_url = environment.resolve_url(value.url)
        value.artifact_problem = None

        async def publish() -> None:
            previous = value.runtime_output
            while True:
                await asyncio.sleep(1)
                if value.runtime_output != previous:
                    self.copies._save(value)
                    previous = value.runtime_output

        publishing = asyncio.create_task(publish())
        try:
            assert value.result_id
            self._eligible(value.workspace_id, value.result_id)
            if preparing:
                timeout = value.setup_timeout_seconds
                for command in value.setup_commands:
                    async with asyncio.timeout(timeout):
                        code = await environment.run(command)
                    if code:
                        raise ApplicationError(
                            "runtime_setup_failed",
                            f"Preparation failed (exit {code}).",
                        )
                value.runtime_state = "ready"
                value.runtime_prepared = True
            else:
                process = asyncio.create_task(environment.run(value.run_command))
                try:
                    if value.ready_command:
                        from flowfield.adapters.result_environment import wait_ready

                        await wait_ready(
                            environment, process, value.ready_command, value.run_timeout_seconds
                        )
                    else:
                        await asyncio.sleep(0.1)
                    if not process.done():
                        value.runtime_state = "running"
                        self.copies._save(value)
                    code = await process
                    if code:
                        raise ApplicationError(
                            "runtime_failed",
                            f"Run command failed (exit {code}).",
                        )
                    value.runtime_state = "finished"
                finally:
                    if not process.done():
                        process.cancel()
                    await asyncio.gather(process, return_exceptions=True)
        except asyncio.CancelledError:
            value.runtime_state = "stopped"
        except TimeoutError:
            value.runtime_state = "failed"
            value.runtime_problem = "Preparation or readiness timed out."
        except Exception as error:
            value.runtime_state = "failed"
            value.runtime_problem = (
                error.message if isinstance(error, ApplicationError) else str(error)
            )
        finally:
            publishing.cancel()
            await asyncio.gather(publishing, return_exceptions=True)
            value.runtime_log_truncated = environment.log_truncated
            if not await environment.close():
                value.runtime_state = "uncertain"
                value.runtime_problem = (
                    "Could not confirm runtime cleanup. Inspect owned processes before retrying."
                )
            elif not preparing and value.outputs:
                from flowfield.outputs import Outputs

                def capture_files() -> list[OutputFile]:
                    assert value.result_id
                    result = Results(self.workspace).get(value.workspace_id, value.result_id)
                    with Outputs(self.workspace).capture_owned(
                        value.workspace_id,
                        result.task_id,
                        value.outputs,
                        {"runtime": str(environment.runtime)},
                        result_id=value.result_id,
                        inspection_id=value.id,
                        runtime_generation=value.runtime_generation,
                    ) as files:
                        with self.workspace.connection(
                            write=True, workspace_id=value.workspace_id
                        ) as db:
                            Outputs.publish(db, files)
                        return files

                capturing = asyncio.create_task(asyncio.to_thread(capture_files))
                try:
                    # Large files must not block event delivery to other browser tabs. Finish
                    # this capture even if Stop arrives again; it owns immutable file bytes.
                    while True:
                        try:
                            value.files = await asyncio.shield(capturing)
                            break
                        except asyncio.CancelledError:
                            if capturing.cancelled():
                                raise
                except (ApplicationError, OSError) as error:
                    value.artifact_problem = str(error)
            self.copies._save(value)

    async def schedule(self) -> None:
        # Stop stale environments even with the worker queue paused.
        for value in list(self.values.values()):
            self._reconcile_cleanup(value)
            if value.runtime_state in ("ready", "preparing", "starting", "running"):
                try:
                    assert value.result_id
                    self._eligible(value.workspace_id, value.result_id, allow_delivery=True)
                except ApplicationError:
                    with contextlib.suppress(ApplicationError):
                        await self.stop(value.workspace_id, value.id)
        with self.workspace.connection() as db:
            candidates = [
                (row[0], row[1])
                for row in db.execute(
                    "SELECT workspace_id,id FROM result_versions WHERE status='ready'"
                )
            ]
        for workspace_id, identity in candidates:
            if identity in self.seen or self.closing:
                continue
            # Candidate validation can finish before independent review. Wait for
            # the task's human-review phase before consuming its automatic prepare.
            try:
                self._eligible(workspace_id, identity)
            except ApplicationError:
                continue
            self.seen.add(identity)
            result = Results(self.workspace).get(workspace_id, identity)
            task = self.workspace.task(workspace_id, result.task_id)
            if not task.project_id or not self.copies.settings(task.project_id).auto_prepare:
                continue
            with contextlib.suppress(ApplicationError):
                await self.prepare(
                    workspace_id,
                    InspectionPrepare(result_id=identity, expected_revision=result.revision),
                )

    async def close(self) -> None:
        self.closing = True
        for identity, value in list(self.values.items()):
            with contextlib.suppress(ApplicationError):
                await self._stop(identity, value)
