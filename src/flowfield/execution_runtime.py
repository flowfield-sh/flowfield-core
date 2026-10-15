"""Active-time owner for one native worker turn, including its reporting reminder."""

import asyncio
import contextlib
from types import TracebackType

from flowfield.adapters.agent_contract import PermissionHandler, PermissionRequest
from flowfield.errors import ApplicationError
from flowfield.execution_limits import ExecutionLimits
from flowfield.execution_models import Run


class ActiveExecution:
    def __init__(self, limits: ExecutionLimits, run: Run):
        self.limits, self.run = limits, run
        self.expired = False
        self.failure: Exception | None = None
        self.watcher: asyncio.Task[None] | None = None
        self.waiters = 0
        self.checks = 0

    def clock(self, active: bool | None) -> bool:
        return self.limits.clock(self.run.project_id, self.run.id, active=active)

    def permission(self, handler: PermissionHandler) -> PermissionHandler:
        async def request(value: PermissionRequest) -> str | None:
            self.waiters += 1
            self.clock(bool(self.checks))
            try:
                return await handler(value)
            finally:
                self.waiters -= 1
                self.clock(not self.waiters or bool(self.checks))

        return request

    async def __aenter__(self) -> "ActiveExecution":
        owner = asyncio.current_task()
        assert owner
        self.clock(True)

        async def watch() -> None:
            while True:
                await asyncio.sleep(0.5)
                try:
                    available = self.clock(None)
                except Exception as error:
                    self.failure = error
                    owner.cancel()
                    return
                if not available:
                    self.expired = True
                    owner.cancel()
                    return

        self.watcher = asyncio.create_task(watch())
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self.watcher
        self.watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.watcher
        if self.failure:
            raise ApplicationError(
                "accounting_unavailable",
                "Execution stopped because its allowance could not be recorded. "
                "Inspect the service before retrying.",
                409,
            ) from self.failure
        self.clock(False)
        if self.expired:
            raise ApplicationError(
                "execution_limit",
                "Active execution allowance reached. Work is preserved; add allowance "
                "in Inbox to continue.",
                409,
            ) from error
