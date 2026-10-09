"""Service ownership for explicit immutable bridge installs; no native model sessions."""

import asyncio
from pathlib import Path

from flowfield.adapters import claude_install, codex_install
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessKind


class HarnessInstalls:
    def __init__(self, directory: Path):
        self.directory = directory
        self.jobs: dict[HarnessKind, asyncio.Task[Path]] = {}
        self.closing = False

    def active(self, kind: HarnessKind) -> bool:
        job = self.jobs.get(kind)
        return job is not None and not job.done()

    async def run(self, kind: HarnessKind) -> Path:
        if self.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        if not self.active(kind):
            self.jobs[kind] = asyncio.create_task(self._install(kind))
            self.jobs[kind].add_done_callback(
                lambda job: job.exception() if not job.cancelled() else None
            )
        # A disconnected browser must not abandon an install or create a second owner.
        return await asyncio.shield(self.jobs[kind])

    async def _install(self, kind: HarnessKind) -> Path:
        install = (
            codex_install.download_install if kind == "codex" else claude_install.download_install
        )
        try:
            return await asyncio.to_thread(install, self.directory)
        except ApplicationError:
            raise
        except Exception as error:
            raise ApplicationError(
                "bridge_install_failed",
                "Bridge installation failed. Check the host and retry.",
                409,
            ) from error

    async def close(self) -> None:
        self.closing = True
        # The existing installer bounds download/archive/version work and owns its
        # cross-process file lock/atomic pointer. Join its threads before service exit.
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
