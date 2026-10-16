"""Human-granted execution of frozen project checks on a run-owned checkout."""

import asyncio
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from flowfield.adapters import local_checks
from flowfield.adapters.agent_contract import PermissionHandler, PermissionRequest
from flowfield.adapters.local_execution import LocalAttempt
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import CheckResult, Record, Run
from flowfield.integration import Integrations

if TYPE_CHECKING:
    from flowfield.application import Workspace


class CheckPermission(Record):
    revision: int = 1
    enabled: bool = False
    settings_revision: int | None = None
    project_path: str | None = None
    project_identity: tuple[int, int] | None = None


class CheckPermissionEdit(Record):
    expected_revision: int = Field(ge=1)
    settings_revision: int = Field(ge=1)
    enabled: bool


class CheckPermissions:
    def __init__(self, store: "Workspace"):
        self.store = store

    def get(self, project: str) -> CheckPermission:
        with self.store.connection() as db:
            return self.read(db, project)

    def read(self, db: sqlite3.Connection, project: str) -> CheckPermission:
        current = self.store._project(db, project)
        row = db.execute(
            "SELECT data FROM worker_check_grants WHERE project_id=?", (project,)
        ).fetchone()
        value = CheckPermission.model_validate_json(row[0]) if row else CheckPermission()
        # Keep grant and settings in one snapshot. A nested read can deadlock
        # against a writer waiting for this transaction to release.
        settings = Integrations(self.store)._settings(db, project)
        try:
            stat = Path(current.path).stat()
            identity = (stat.st_dev, stat.st_ino)
        except OSError:
            identity = None
        if (
            value.settings_revision != settings.revision
            or value.project_path != current.path
            or value.project_identity != identity
        ):
            value.enabled = False
        return value

    def configure(self, project: str, request: CheckPermissionEdit) -> CheckPermission:
        with self.store.connection(write=True, project_id=project) as db:
            current = self.store._project(db, project)
            row = db.execute(
                "SELECT data FROM worker_check_grants WHERE project_id=?", (project,)
            ).fetchone()
            previous = CheckPermission.model_validate_json(row[0]) if row else CheckPermission()
            self.store._current(previous.revision, request.expected_revision)
            # Concurrent Coordinator edits cannot retarget this settings-bound grant.
            settings = Integrations(self.store)._settings(db, project)
            self.store._current(settings.revision, request.settings_revision)
            if request.enabled and not settings.checks:
                raise ApplicationError(
                    "checks_missing",
                    "Save project check commands before enabling worker checks.",
                    409,
                )
            stat = Path(current.path).stat() if request.enabled else None
            value = CheckPermission(
                revision=previous.revision + 1,
                enabled=request.enabled,
                settings_revision=settings.revision,
                project_path=current.path,
                project_identity=(stat.st_dev, stat.st_ino) if stat else None,
            )
            db.execute(
                "INSERT INTO worker_check_grants VALUES (?,?) ON CONFLICT(project_id) "
                "DO UPDATE SET data=excluded.data",
                (project, value.model_dump_json()),
            )
            return value

    def require(self, run: Run) -> None:
        grant = self.get(run.project_id)
        if not grant.enabled or grant.settings_revision != run.check_settings_revision:
            raise ApplicationError(
                "check_permission_required",
                "Saved project checks need human permission in Project settings → "
                "Integration. Changed commands require a fresh attempt; do not bypass "
                "native permissions.",
                403,
            )


class WorkerChecks:
    def __init__(self, execution: Execution, run: Run, environment: LocalAttempt):
        self.execution, self.run, self.environment = execution, run, environment
        self.job: asyncio.Task[list[CheckResult]] | None = None
        self.cleanup_uncertain = False

    def permission(self, server: str, fallback: PermissionHandler) -> PermissionHandler:
        async def request(value: PermissionRequest) -> str | None:
            observed = value.managed_tool
            if (
                observed
                and observed.server == server
                and observed.name == "run_project_checks"
                and observed.arguments == {}
            ):
                try:
                    self.execution.worker_access(self.run.project_id, self.run.id)
                    CheckPermissions(self.execution.workspace).require(self.run)
                except ApplicationError:
                    pass
                else:
                    # Choose only an offered one-call grant. The operation rechecks authority
                    # before execution; this cannot authorize another server/tool or native shell.
                    return next(
                        (identity for identity, _, kind in value.options if kind == "allow_once"),
                        None,
                    )
            return await fallback(value)

        return request

    async def call(self) -> str:
        self.execution.worker_access(self.run.project_id, self.run.id)
        CheckPermissions(self.execution.workspace).require(self.run)
        if self.job:
            raise ApplicationError("checks_busy", "Wait for the current project checks.", 409)
        if not self.run.project_checks:
            raise ApplicationError(
                "checks_missing", "This attempt has no configured project checks.", 409
            )
        self.job = asyncio.create_task(
            local_checks.run_checks(
                self.environment,
                self.run.project_checks,
                self.run.check_timeout_seconds,
                lambda pid: self.execution.save_local(
                    self.run.id, {**self.execution.local(self.run.id), "check_process": pid}
                ),
            )
        )
        try:
            checks = await self.job
            with self.execution.workspace.connection(
                write=True, project_id=self.run.project_id
            ) as db:
                current = self.execution._run(db, self.run.project_id, self.run.id)
                current.project_check_results = checks
                self.execution._save(db, current)
            import json

            return json.dumps(
                {
                    "scope": "Current worker checkout; final candidate validation "
                    "remains separate.",
                    "settings_revision": self.run.check_settings_revision,
                    "checks": [
                        {
                            **check.model_dump(),
                            "output": check.output[:300],
                            "truncated": check.truncated or len(check.output) > 300,
                        }
                        for check in checks
                    ],
                    "details": "read_context project_checks pages the latest full retained output",
                }
            )
        except ApplicationError as error:
            self.cleanup_uncertain |= error.code == "command_cleanup_uncertain"
            raise
        finally:
            self.job = None

    async def close(self) -> None:
        if self.job:
            self.job.cancel()
            results = await asyncio.gather(self.job, return_exceptions=True)
            self.cleanup_uncertain |= any(
                isinstance(item, ApplicationError) and item.code == "command_cleanup_uncertain"
                for item in results
            )
