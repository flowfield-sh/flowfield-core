"""Execution boundary for result copies; task, approval and artifact records stay in the app."""

import asyncio
from pathlib import Path
from typing import Protocol


class ResultEnvironment(Protocol):
    runtime: Path
    log: Path
    log_truncated: bool

    async def run(self, command: str) -> int: ...
    async def close(self) -> bool: ...
    def resolve_url(self, url: str) -> str: ...


async def wait_ready(
    environment: ResultEnvironment,
    process: asyncio.Task[int],
    command: str,
    timeout: int,
) -> None:
    async with asyncio.timeout(timeout):
        while True:
            probe = asyncio.create_task(environment.run(command))
            try:
                await asyncio.wait((process, probe), return_when=asyncio.FIRST_COMPLETED)
                if process.done():
                    raise RuntimeError(
                        f"Run command exited before readiness (exit {await process})."
                    )
                if await probe == 0:
                    return
            finally:
                if not probe.done():
                    probe.cancel()
                await asyncio.gather(probe, return_exceptions=True)
            await asyncio.sleep(0.25)
