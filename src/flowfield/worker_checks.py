"""Run-bound execution of frozen project checks on a managed checkout."""

import asyncio

from flowfield.adapters import local_checks
from flowfield.adapters.agent_contract import PermissionHandler, PermissionRequest
from flowfield.adapters.local_execution import LocalAttempt
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import CheckResult, Run


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
                    self.execution.worker_access(self.run.workspace_id, self.run.id)
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
        self.execution.worker_access(self.run.workspace_id, self.run.id)
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
                write=True, workspace_id=self.run.workspace_id
            ) as db:
                current = self.execution._run(db, self.run.workspace_id, self.run.id)
                current.project_check_results = checks
                self.execution._save(db, current)
            import json

            from flowfield.check_logs import sections

            return json.dumps(
                {
                    "scope": "Current worker checkout; final candidate validation "
                    "remains separate.",
                    "settings_revision": self.run.check_settings_revision,
                    "checks": [
                        {
                            **check,
                            "output": str(check["output"])[:300],
                            "truncated": bool(check["truncated"])
                            or len(str(check["output"])) > 300,
                        }
                        for check in sections(checks)
                    ],
                    "details": "Output is already retained. read_context section=project_checks "
                    "reads receipts/previews; each log_section reads its verified log in pages. "
                    "log_truncated means the retained log reached its byte cap. Do not rerun "
                    "passing checks just to capture output or attach duplicate logs.",
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
