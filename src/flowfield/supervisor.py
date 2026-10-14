"""Service-owned scheduling and the narrow worker bridge. No background planning model."""

import asyncio
import contextlib
import json
import os
import subprocess
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO

from flowfield.adapters import git_integration as gitops
from flowfield.adapters import local_checks
from flowfield.adapters.agent_contract import Agent
from flowfield.adapters.agent_permissions import permission_handler
from flowfield.adapters.agent_selection import create
from flowfield.adapters.coordinator_directory import prepare as coordinator_directory
from flowfield.adapters.git_workspace import GitWorkspace, contains
from flowfield.adapters.harness_host import HarnessChecks
from flowfield.adapters.local_execution import LocalAttempt, LocalHost
from flowfield.agent_models import AgentChoice, AgentSettingsEdit, AgentSettingsView
from flowfield.application import Workspace
from flowfield.attachments import Attachments
from flowfield.catalogs import Catalogs
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import (
    CheckResult,
    ModelOption,
    Run,
    RunAction,
    RunLocation,
    RunStatus,
    SettingsEdit,
    WorkerResult,
    WorkerSettings,
    WorkspaceWorkerSettings,
)
from flowfield.harness_models import HarnessKind
from flowfield.harness_settings import HarnessSettings
from flowfield.integration import Integrations
from flowfield.permissions import Permissions
from flowfield.results import Results
from flowfield.run_activity import ActivityRecorder, ActivityUpdate
from flowfield.setup_validation import SetupValidation
from flowfield.storage import acquire_lock
from flowfield.worker_context import brief_context
from flowfield.worker_tools import WorkerBridge
from flowfield.workspaces import Workspaces


def process_stamp(pid: int) -> str:
    value = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=5
    )
    return value.stdout.strip() if value.returncode == 0 else ""


class Supervisor:
    def __init__(self, workspace: Workspace):
        self.workspace, self.execution = workspace, Execution(workspace)
        self.integrations = Integrations(workspace)
        self.results = Results(workspace)
        self.setup_validation = SetupValidation(workspace)
        self.delivery_jobs: dict[str, asyncio.Task[None]] = {}
        self.clients: dict[str, Agent] = {}
        self.permissions = Permissions(workspace)
        self.harness_checks = HarnessChecks(workspace.directory)
        from flowfield.coordinator import Coordinator

        self.coordinator = Coordinator(self)
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.setup_jobs: dict[str, asyncio.Task[list[CheckResult]]] = {}
        self.catalogs = Catalogs(workspace)
        self.loop_task: asyncio.Task[None] | None = None
        self.lock: BinaryIO | None = None
        self.closing = False

    async def start(self) -> None:
        self.lock = acquire_lock(self.workspace.directory, ".execution.lock")
        try:
            # A different build may have upgraded between Workspace construction and start.
            # connection() rechecks the schema under the shared maintenance lock.
            with self.workspace.connection():
                pass
            self.execution.restart()
            self.permissions.restart()
            self.coordinator.store.restart()
            self.catalogs.restart()
            recovery = asyncio.create_task(asyncio.to_thread(self.integrations.restart))
            try:
                await asyncio.shield(recovery)
            except asyncio.CancelledError:
                # Cancellation must not release ownership while recovery's thread still writes.
                while not recovery.done():
                    try:
                        await asyncio.shield(recovery)
                    except asyncio.CancelledError:
                        pass
                recovery.result()
                raise
            self.loop_task = asyncio.create_task(self._schedule())
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    async def model_options(
        self,
        *,
        refresh: bool = False,
        harness: HarnessKind = "codex",
        project_id: str | None = None,
        project_path: str | None = None,
    ) -> list[ModelOption]:
        if self.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        return await self.catalogs.run(
            HarnessSettings(self.workspace).get(harness),
            project_id=project_id,
            project_path=project_path,
            refresh=refresh,
        )

    async def configure(self, project_id: str, request: SettingsEdit) -> WorkerSettings:
        await self.validate_agent_choice(request.selection, project_id)
        return self.execution.configure(project_id, request)

    async def workspace_models(
        self, identity: str, *, harness: HarnessKind = "codex", refresh: bool = False
    ) -> list[ModelOption]:
        path = str(coordinator_directory(self.workspace, identity))
        return await self.model_options(harness=harness, project_path=path, refresh=refresh)

    async def configure_workspace_workers(
        self, identity: str, request: SettingsEdit
    ) -> WorkspaceWorkerSettings:
        spaces = Workspaces(self.workspace)
        self.workspace._current(spaces.workers(identity).revision, request.expected_revision)
        await self.validate_agent_choice(
            request.selection, project_path=str(coordinator_directory(self.workspace, identity))
        )
        return spaces.configure_workers(identity, request)

    async def configure_workspace_coordinator(
        self, identity: str, request: AgentSettingsEdit
    ) -> AgentSettingsView:
        spaces = Workspaces(self.workspace)
        self.workspace._current(spaces.coordinator(identity).revision, request.expected_revision)
        if request.selection:
            await self.validate_agent_choice(
                request.selection, project_path=str(coordinator_directory(self.workspace, identity))
            )
        return spaces.configure_coordinator(identity, request)

    async def validate_agent_choice(
        self, choice: AgentChoice, project_id: str | None = None, *, project_path: str | None = None
    ) -> None:
        models = await self.model_options(
            project_id=project_id, project_path=project_path, harness=choice.harness
        )
        if not any(
            item.id == choice.model
            and (choice.effort in item.efforts if item.efforts else choice.effort is None)
            and (choice.mode is None or choice.mode in {mode.id for mode in item.modes})
            and (not choice.fast or item.fast)
            for item in models
        ):
            raise ApplicationError(
                "model_unavailable",
                "Choose a model and supported controls returned by this harness for the project.",
                409,
            )

    def _available(
        self, project_id: str, repository: Path, head: str, branch: str | None = None
    ) -> dict[str, set[str]]:
        with self.workspace.connection() as db:
            followups = []
            for row in db.execute(
                "SELECT data FROM work_runs r WHERE project_id=? "
                "AND status IN ('changes_requested','failed','stopped','waiting_for_input') "
                "AND NOT EXISTS (SELECT 1 FROM work_runs newer WHERE newer.project_id=r.project_id "
                "AND newer.task_id=r.task_id AND newer.number>r.number)",
                (project_id,),
            ):
                previous = Run.model_validate_json(row[0])
                correction = previous.next_correction
                if previous.status != "changes_requested":
                    correction = previous.correction
                followups.extend(
                    [
                        previous.input_checkpoint,
                        previous.input_base_commit,
                        correction.base_commit if correction else previous.result_commit,
                    ]
                )
        commits = self.integrations.available(project_id, head, branch)
        return {
            base: {commit for commit in commits if commit and contains(repository, commit, base)}
            for base in {head, *followups}
            if base
        }

    async def _schedule(self) -> None:
        while not self.closing:
            await self._schedule_once()
            await asyncio.sleep(0.5)

    async def _schedule_once(self) -> None:
        # Repository preparation remains project-local. A failed repository cannot
        # consume the shared queue; fresh claims follow workspace board order.
        ready: dict[str, Path] = {}
        problems: dict[str, str | None] = {}
        for project in self.workspace.projects():
            try:
                if project.id not in self.delivery_jobs:
                    delivery = asyncio.create_task(
                        asyncio.to_thread(self._process_delivery, project.id)
                    )
                    self.delivery_jobs[project.id] = delivery
                    delivery.add_done_callback(partial(self._delivery_finished, project.id))
                await asyncio.to_thread(self.integrations.refresh_availability, project.id)
                if not self.execution.settings(project.id).enabled:
                    continue
                ready[project.id] = Path(project.path)
                problems[project.id] = None
            except ApplicationError as error:
                problems[project.id] = error.message
            except Exception:
                problems[project.id] = (
                    "Scheduling failed. Pause the queue and inspect the local service "
                    "before retrying."
                )
        started: set[str] = set()
        for project_id, task_id in self.execution.scheduling_candidates():
            if self.closing:
                break
            if project_id not in ready:
                continue
            repository = ready[project_id]
            try:
                settings = self.integrations.for_task(project_id, task_id)
                head = await asyncio.to_thread(self.integrations.head, project_id, task_id)
                available = await asyncio.to_thread(
                    self._available, project_id, repository, head, settings.target_branch
                )
                run = self.execution.claim(project_id, head, available, task_id=task_id)
                if run:
                    task = asyncio.create_task(self._execute(run, repository))
                    self.jobs[run.id] = task
                    task.add_done_callback(partial(self._job_finished, run.id))
                    started.add(project_id)
            except ApplicationError as error:
                problems[project_id] = error.message
            except Exception:
                problems[project_id] = (
                    "Scheduling failed. Pause the queue and inspect the local service "
                    "before retrying."
                )
        for project_id, problem in problems.items():
            self.execution.queue_problem(project_id, None if project_id in started else problem)

    def _job_finished(self, identity: str, task: asyncio.Task[None]) -> None:
        self.jobs.pop(identity, None)

    def _process_delivery(self, project_id: str) -> None:
        from flowfield.milestone_promotion import MilestonePromotions

        self.results.process(project_id)
        MilestonePromotions(self.workspace).process(project_id)

    def _delivery_finished(self, identity: str, task: asyncio.Task[None]) -> None:
        self.delivery_jobs.pop(identity, None)
        if not task.cancelled() and task.exception():
            self.execution.queue_problem(
                identity,
                "Result processing failed; inspect the service and preserved result.",
            )

    async def _execute(self, run: Run, repository: Path) -> None:
        client: Agent | None = None
        environment: LocalAttempt | None = None
        scope_stack = contextlib.AsyncExitStack()
        activity: ActivityRecorder | None = None
        applied_agent: AgentChoice | None = None
        terminal: RunStatus = "failed"
        problem: str | None = None
        result: WorkerResult | None = None
        commit: str | None = None
        input_checkpoint: str | None = None
        starting_commit = run.input_base_commit or run.base_commit
        try:
            sections = self.execution.assignment(run.project_id, run.id)
            for prerequisite in json.loads(sections["prerequisites"]):
                if (
                    prerequisite.get("project_id", run.project_id) == run.project_id
                    and prerequisite["commit"]
                    and not await asyncio.to_thread(
                        contains, repository, prerequisite["commit"], starting_commit
                    )
                ):
                    raise ApplicationError(
                        "baseline_missing_prerequisite",
                        "This retry's code baseline lacks a prerequisite result. "
                        "Integrate the required code before starting a fresh attempt.",
                    )
            from flowfield.adapters.agent_mcp import serve_scope
            from flowfield.agent_tools import worker_scope

            environment = await asyncio.to_thread(
                LocalHost(os.environ).prepare,
                self.workspace.directory,
                repository,
                run.id,
                starting_commit,
            )
            metadata: dict[str, Any] = {
                "runtime_kind": "local",
                "root": str(environment.root),
                "checkout": str(environment.checkout),
                "runtime": str(environment.runtime),
                "common_git": str(environment.common_git),
            }
            self.execution.save_local(run.id, metadata)
            choice = (
                run.agent_settings.choice
                if run.agent_settings
                else AgentChoice(model=run.model, effort=run.effort)
            )
            client = create(
                choice,
                self.workspace.directory,
                environment.checkout,
                environment.launch_environment(),
                registration=run.agent_settings.registration if run.agent_settings else None,
            )
            self.clients[run.id] = client
            bridge = WorkerBridge(self.execution, run, client)
            server = await scope_stack.enter_async_context(serve_scope(worker_scope(bridge)))
            # Persist before launch: after a crash, missing PID is not a cleanup receipt.
            metadata["native_launch_started"] = True
            metadata["harness_launch"] = client.launch.model_dump() if client.launch else None
            self.execution.save_local(run.id, metadata)
            await client.start([server])
            assert client.process
            metadata.update(
                pid=client.process.pid,
                process_stamp=await asyncio.to_thread(process_stamp, client.process.pid),
            )
            self.execution.save_local(run.id, metadata)
            applied_agent = await client.configure(choice)
            if getattr(client, "supports_activity", False):
                activity = ActivityRecorder(self.workspace, run.project_id, run.id)
                client.on_activity = activity.emit
                activity.emit(
                    ActivityUpdate(key="started", kind="status", text="Worker environment ready.")
                )
            if self.execution.get(run.project_id, run.id).status == "stopping":
                raise ApplicationError("worker_stopping", "Stop requested; work preserved.", 409)
            setup_job = asyncio.create_task(
                local_checks.run_checks(
                    environment,
                    run.setup_commands,
                    run.setup_timeout_seconds,
                    lambda pid: self.execution.save_local(
                        run.id, {**self.execution.local(run.id), "setup_process": pid}
                    ),
                )
            )
            self.setup_jobs[run.id] = setup_job
            try:
                setup = await setup_job
            finally:
                self.setup_jobs.pop(run.id, None)
            self.execution.record_setup(run.project_id, run.id, setup)
            if any(check.exit_code for check in setup):
                raise ApplicationError(
                    "runtime_setup_failed",
                    local_checks.command_problem(setup, []),
                )
            if setup and not await asyncio.to_thread(
                gitops.unchanged, environment, starting_commit
            ):
                raise ApplicationError(
                    "runtime_changed_source",
                    "Setup changed source files. Fix setup commands; inspect preserved changes.",
                )
            self.execution.started(
                run.project_id, run.id, applied_agent=applied_agent, harness_launch=client.launch
            )
            sections = self.execution.assignment(run.project_id, run.id)
            brief: dict[str, Any] = {
                "task": sections["title"],
                "task_id": run.task_id,
                "task_key": run.task_key,
                "base_commit": run.base_commit,
                "agreement_revision": run.agreement_revision,
                "completion": run.completion,
                "target_branch": run.target_branch,
                **brief_context(sections),
            }
            from flowfield.worker_instructions import completion_reminder, instructions

            brief["flowfield_connection"] = server.name
            brief["instructions"] = instructions(
                sections, server=server.name, completion=run.completion
            )
            assert client.session_id
            async with self.permissions.turn(
                run.project_id,
                "worker",
                session_id=client.session_id,
                turn_id=run.id,
                run_id=run.id,
            ) as turn:
                outcome = await client.prompt(
                    json.dumps(brief, ensure_ascii=False),
                    permission_handler(turn),
                    attachments=Attachments(self.workspace).inputs(
                        run.project_id,
                        run.task_id,
                        sections.get("feedback", "") + sections.get("input", ""),
                    ),
                )
                if (
                    outcome.get("status") == "completed"
                    and not bridge.result
                    and not bridge.question_id
                    and not self.closing
                    and not client.stopping
                    and self.execution.get(run.project_id, run.id).status == "running"
                ):
                    if activity:
                        activity.emit(
                            ActivityUpdate(
                                key="completion-reminder",
                                kind="status",
                                text="Worker ended without reporting an outcome. "
                                "Asking once for a result or blocking question.",
                            )
                        )
                    outcome = await client.prompt(
                        completion_reminder(server.name), permission_handler(turn)
                    )
            confirmed = await client.stop()
            if not confirmed:
                terminal, problem = (
                    "uncertain",
                    (
                        "Could not confirm all tracked commands stopped. Keep this slot "
                        "reserved until reconciled."
                    ),
                )
            elif bridge.question_id:
                input_checkpoint, ignored = await asyncio.to_thread(
                    environment.snapshot, starting_commit
                )
                terminal, problem = (
                    "waiting_for_input",
                    "Work preserved; waiting for an answer and an eligible continuation."
                    + (
                        " Ignored runtime files remain in the previous workspace."
                        if ignored
                        else ""
                    ),
                )
            elif outcome.get("status") == "completed" and bridge.result:
                result = bridge.result
                commit, ignored = await asyncio.to_thread(environment.snapshot, starting_commit)
                assert commit
                # Generated-file evidence belongs to execution diagnostics, not user limitations.
                self.execution.excluded_files(run.project_id, run.id, ignored)
                terminal = "in_review"
            elif self.execution.get(run.project_id, run.id).status == "stopping":
                terminal, problem = (
                    "stopped",
                    "Worker and tracked commands stopped; work preserved.",
                )
            else:
                problem = (
                    "Worker ended without a submitted result. Inspect preserved work "
                    "and explicitly retry."
                )
        except asyncio.CancelledError:
            terminal, problem = "stopped", "Execution stopped; work preserved."
            if client and not await client.stop():
                terminal, problem = "uncertain", "Native cleanup could not be confirmed."
        except Exception as error:
            problem = (
                error.message
                if isinstance(error, ApplicationError)
                else (
                    f"Worker preparation/execution failed ({type(error).__name__}). "
                    "Work is preserved."
                )
            )
            if isinstance(error, ApplicationError) and error.code == "command_cleanup_uncertain":
                terminal = "uncertain"
            elif self.execution.get(run.project_id, run.id).status == "stopping":
                terminal = "stopped"
            if client and not await client.stop():
                terminal, problem = (
                    "uncertain",
                    problem + " Tracked process cleanup could not be confirmed.",
                )
        finally:
            if client:
                await client.close()
                self.execution.save_local(
                    run.id,
                    {
                        **self.execution.local(run.id),
                        "native_cleanup_confirmed": client.cleanup_confirmed,
                    },
                )
            try:
                await scope_stack.aclose()
            except (Exception, asyncio.CancelledError):
                terminal, problem = "uncertain", "Scoped tool shutdown could not be confirmed."
            if activity:
                captured = commit or input_checkpoint
                if environment and captured:
                    from flowfield.adapters.activity_diff import captured_changes

                    try:
                        summary = await asyncio.to_thread(
                            captured_changes, environment.checkout, starting_commit, captured
                        )
                    except Exception:
                        summary = "File summary unavailable; captured evidence is preserved."
                    activity.emit(ActivityUpdate(key="captured-files", kind="tool", text=summary))
                with contextlib.suppress(Exception):
                    await activity.close()
            self.clients.pop(run.id, None)
            with contextlib.suppress(ApplicationError):
                self.execution.finish(
                    run.project_id,
                    run.id,
                    terminal,
                    result=result,
                    commit=commit,
                    problem=problem,
                    input_checkpoint=input_checkpoint,
                )

    async def stop(self, project_id: str, run_id: str, request: RunAction) -> Run:
        run = self.execution.stop_requested(project_id, run_id, request)
        self.permissions.close_run(project_id, run_id)
        if run.status == "stopped":
            return run
        client = self.clients.get(run_id)
        setup = self.setup_jobs.get(run_id)
        if setup:
            setup.cancel()
            await asyncio.gather(setup, return_exceptions=True)
        if client:
            confirmed = await client.stop()
            task = self.jobs.get(run_id)
            if task:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 20)
            current = self.execution.get(project_id, run_id)
            if current.status == "stopping":
                return self.execution.finish(
                    project_id,
                    run_id,
                    "stopped" if confirmed else "uncertain",
                    problem="Stop requested; work preserved.",
                )
            return current
        if run_id in self.jobs:
            return run  # Preparation sees the stop before starting a model turn.
        metadata = self.execution.local(run_id)
        if metadata.get("runtime_kind") == "local" and metadata.get("native_launch_started"):
            confirmed = metadata.get("native_cleanup_confirmed") is True and not metadata.get(
                "setup_process"
            )
            return self.execution.finish(
                project_id,
                run_id,
                "stopped" if confirmed else "uncertain",
                problem="Recorded native cleanup confirmed; work preserved."
                if confirmed
                else "The service lost the live native owner. Harness absence does not prove "
                "its tools stopped. Work and capacity remain reserved; inspect the local "
                "processes before recovery. No process was signalled from a saved PID.",
            )
        if metadata.get("setup_process") or (
            metadata.get("native_launch_started") and not metadata.get("native_cleanup_confirmed")
        ):
            return self.execution.finish(
                project_id,
                run_id,
                "uncertain",
                problem="The service lost contact with running tools. Harness absence "
                "does not prove those tools stopped. Work and capacity remain reserved; "
                "inspect the local processes before recovery.",
            )
        return self.execution.finish(
            project_id,
            run_id,
            "stopped",
            problem=(
                "Owned harness is absent or stopped. Previous work preserved; retry is explicit."
            ),
        )

    def environment(self, run_id: str) -> LocalAttempt | None:
        data = self.execution.local(run_id)
        if not data:
            return None
        if data.get("runtime_kind") == "local":
            return LocalHost(os.environ).restore(
                run_id,
                GitWorkspace(Path(data["root"]), Path(data["checkout"]), Path(data["common_git"])),
                Path(data["runtime"]),
            )
        return None

    def location(self, project_id: str, run_id: str) -> RunLocation:
        run = self.execution.get(project_id, run_id)
        environment = self.environment(run_id)
        return (
            environment.location(run.base_commit, run.result_commit)
            if environment
            else RunLocation()
        )

    async def close(self) -> None:
        self.closing = True
        await self.harness_checks.close()
        await self.coordinator.close()
        self.permissions.close()
        await self.catalogs.close()
        await self.setup_validation.close()
        if self.loop_task:
            self.loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.loop_task
        for run in self.execution.active():
            if run.id in self.jobs or run.id in self.clients:
                with contextlib.suppress(Exception):
                    await self.stop(
                        run.project_id, run.id, RunAction(expected_revision=run.revision)
                    )
        if self.jobs:
            await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
        if self.delivery_jobs:
            await asyncio.gather(*list(self.delivery_jobs.values()), return_exceptions=True)
        if self.lock:
            self.lock.close()
            self.lock = None
