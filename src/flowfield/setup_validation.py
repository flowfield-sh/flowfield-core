"""Explicit, model-free setup check using the same command boundary as managed workers."""

import asyncio
import fcntl
import os
import re
from functools import partial
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict, Field

from flowfield.adapters import git_checkout, local_checks
from flowfield.adapters import git_integration as gitops
from flowfield.adapters.local_execution import LocalHost
from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult, Record
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationSettings


class SetupCheckRequest(Record):
    expected_revision: int = Field(ge=1)


class SetupCheck(Record):
    model_config = ConfigDict(extra="ignore")
    project_id: str
    settings_revision: int
    id: str
    commit: str
    created_at: str
    status: Literal["checking", "passed", "failed", "uncertain"] = "checking"
    stale: bool = False
    problem: str | None = None
    checkout_problem: str | None = None
    workspace: str | None = None
    setup: list[CheckResult] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    settings: IntegrationSettings
    previous_check_id: str | None = None
    # Process transport remains local diagnostic evidence; never passed to a model.
    pid: int | None = None


class SetupValidation:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.jobs: set[asyncio.Task[object]] = set()
        self.active: set[str] = set()
        self.closing = False

    def _path(self, project_id: str) -> Path:
        self.workspace.project(project_id)
        return self.workspace.directory / f"setup-{project_id}.json"

    def _save(self, value: SetupCheck) -> None:
        path = self._path(value.project_id)
        temporary = path.with_suffix(".pending")
        with temporary.open("w") as stream:
            stream.write(value.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def get(self, project_id: str, check_id: str | None = None) -> SetupCheck | None:
        path = self._path(project_id)
        if check_id is not None and not re.fullmatch(r"[a-f0-9]{32}", check_id):
            raise ApplicationError("invalid_setup_check", "Invalid setup check identity.", 422)
        if not path.exists():
            if check_id:
                raise ApplicationError("setup_check_missing", "Setup check not found.", 404)
            return None
        value = SetupCheck.model_validate_json(path.read_text())
        if check_id and value.id != check_id:
            path = path.parent / "setup-history" / project_id / f"{check_id}.json"
            if not path.exists():
                raise ApplicationError("setup_check_missing", "Setup check not found.", 404)
            value = SetupCheck.model_validate_json(path.read_text())
        settings = Integrations(self.workspace).settings(project_id)
        # Current checkout readiness is independent of historical command evidence.
        # Reuse delivery's read-only guards; this is not an authorization to deliver.
        repo = Path(self.workspace.project(project_id).path)
        try:
            branch = settings.target_branch or ""
            git_checkout.verify(
                repo, branch, gitops.target(repo, branch), git_checkout.binding(repo)
            )
            value.checkout_problem = None
        except ApplicationError as error:
            value.checkout_problem = error.message
        value.stale = settings.revision != value.settings_revision
        if not value.stale:
            try:
                head = gitops.target(
                    Path(self.workspace.project(project_id).path), settings.target_branch or ""
                )
                value.stale = head != value.commit
            except ApplicationError:
                value.stale = True
        if value.status == "checking" and project_id not in self.active:
            value.status = "uncertain" if value.pid else "failed"
            value.problem = (
                (
                    "Setup validation was interrupted. Preserved files and process "
                    "evidence must be checked before repeating commands."
                )
                if value.status == "uncertain"
                else "Setup validation was interrupted before execution; run it again."
            )
        return value

    async def check(self, project_id: str, request: SetupCheckRequest) -> SetupCheck:
        if self.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        task = asyncio.current_task()
        assert task
        self.jobs.add(task)
        try:
            with self._path(project_id).with_suffix(".lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ApplicationError(
                        "setup_busy", "Setup validation is already running.", 409
                    ) from error
                previous = self.get(project_id)
                if previous and previous.status == "uncertain":
                    raise ApplicationError(
                        "setup_uncertain",
                        previous.problem or "Check the previous process before retrying.",
                        409,
                    )
                settings = Integrations(self.workspace).settings(project_id)
                self.workspace._current(settings.revision, request.expected_revision)
                if not settings.target_branch:
                    raise ApplicationError("setup_missing", "Choose a destination first.", 409)
                repo = Path(self.workspace.project(project_id).path)
                commit = gitops.target(repo, settings.target_branch)
                value = SetupCheck(
                    project_id=project_id,
                    id=uuid4().hex,
                    settings_revision=settings.revision,
                    commit=commit,
                    created_at=now(),
                    settings=settings,
                    previous_check_id=previous.id if previous else None,
                )
                if previous:
                    # Archive the exact observation before replacing the current check.
                    history = self._path(project_id).parent / "setup-history" / project_id
                    history.mkdir(parents=True, exist_ok=True)
                    archived = history / f"{previous.id}.json"
                    temporary = archived.with_suffix(".pending")
                    with temporary.open("w") as stream:
                        stream.write(previous.model_dump_json())
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(archived)
                self.active.add(project_id)
                self._save(value)
                try:
                    prepare = partial(
                        LocalHost(os.environ).prepare,
                        self.workspace.directory / "setup-work",
                        repo,
                        value.id,
                        commit,
                    )
                    preparing = asyncio.create_task(asyncio.to_thread(prepare))
                    try:
                        environment = await asyncio.shield(preparing)
                    except asyncio.CancelledError:
                        # A Python thread cannot be cancelled. Retain its completed copy
                        # before releasing ownership or allowing another validation.
                        environment = await preparing
                        value.workspace = str(environment.checkout)
                        raise
                    value.workspace = str(environment.checkout)

                    def record_process(pid: int | None) -> None:
                        value.pid = pid
                        self._save(value)

                    value.setup = await local_checks.run_checks(
                        environment,
                        settings.setup_commands,
                        settings.setup_timeout_seconds,
                        record_process,
                    )
                    self._save(value)
                    if not any(c.exit_code for c in value.setup):
                        value.checks = await local_checks.run_checks(
                            environment,
                            settings.checks,
                            settings.check_timeout_seconds,
                            record_process,
                        )
                    clean = await asyncio.to_thread(gitops.unchanged, environment, commit)
                    passed = clean and not any(c.exit_code for c in [*value.setup, *value.checks])
                    value.status = "passed" if passed else "failed"
                    value.problem = (
                        None
                        if passed
                        else (
                            "Setup or validation changed source files; inspect the preserved copy."
                            if not clean
                            else local_checks.command_problem(value.setup, value.checks)
                        )
                    )
                except asyncio.CancelledError:
                    value.status = "failed"
                    value.problem = "Setup validation stopped; files preserved."
                    raise
                except (ApplicationError, OSError) as error:
                    value.status = (
                        "uncertain"
                        if isinstance(error, ApplicationError)
                        and error.code == "command_cleanup_uncertain"
                        else "failed"
                    )
                    value.problem = (
                        error.message
                        if isinstance(error, ApplicationError)
                        else (
                            "Setup could not run; inspect the local tool configuration and "
                            "preserved files."
                        )
                    )
                finally:
                    self._save(value)
                    self.active.discard(project_id)
                return self.get(project_id) or value
        finally:
            self.jobs.discard(task)

    async def close(self) -> None:
        self.closing = True
        jobs = list(self.jobs)
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
