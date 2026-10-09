"""Owned POSIX process groups, shared by local execution and native transports.

Group exit does not prove that detached descendants exited. Never recover ownership
from a persisted PID or terminate a process by name. Streams belong to the caller,
which must drain them with bounded retention while the process runs.
"""

import asyncio
import contextlib
import math
import os
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path


class LocalProcess:
    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self._close_task: asyncio.Task[bool] | None = None

    @classmethod
    async def start(
        cls, command: Sequence[str], *, cwd: Path, env: Mapping[str, str], limit: int = 65536
    ) -> "LocalProcess":
        if os.name != "posix":
            raise RuntimeError("Owned local process groups currently require POSIX")
        if not command or not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError("A command and existing absolute working directory are required")
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=dict(env),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=limit,
            )
        )
        try:
            return cls(await asyncio.shield(spawn))
        except asyncio.CancelledError:
            # A cancelled launch must still adopt and stop the process it created.
            async def cleanup() -> LocalProcess:
                owner = cls(await spawn)
                await owner.close()
                return owner

            cleanup_task = asyncio.create_task(cleanup())
            while True:
                try:
                    owner = await asyncio.shield(cleanup_task)
                    break
                except asyncio.CancelledError:
                    if cleanup_task.cancelled():
                        raise
                    # Repeated caller cancellation must not discard the new owner.
            # Retain the handle even if cleanup could not confirm group exit.
            raise LocalLaunchCancelled(owner) from None

    async def close(self, *, timeout: float = 5) -> bool:
        """Return whether the owned group exited; cancellation cannot abandon cleanup."""
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Stop timeout must be positive and finite")
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stop(timeout))
        return await asyncio.shield(self._close_task)

    def _group_exists(self) -> bool:
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass  # Still present, but cannot prove ownership/termination.
        return True

    async def _stop(self, timeout: float) -> bool:
        # The leader may already have exited while ordinary children remain.
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.process.pid, signum)
            except ProcessLookupError:
                break
            except PermissionError:
                return False
            deadline = asyncio.get_running_loop().time() + timeout
            while self._group_exists() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            if not self._group_exists():
                break
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout):
                await self.process.wait()
        return not self._group_exists() and self.process.returncode is not None


class LocalLaunchCancelled(asyncio.CancelledError):
    """A cancelled caller still receives ownership of any process it launched."""

    def __init__(self, owner: LocalProcess):
        super().__init__("Local launch cancelled; process cleanup was requested")
        self.owner = owner
