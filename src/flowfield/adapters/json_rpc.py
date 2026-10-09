"""Bounded JSON-line RPC over an exactly owned local process, with no replay."""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from flowfield.adapters.local_process import LocalLaunchCancelled, LocalProcess

MAX_FRAME = 256 * 1024
MAX_INPUT = 24 * 1024 * 1024


class NativeError(RuntimeError):
    pass


class JsonRpc:
    def __init__(
        self,
        notification: Callable[[str, dict[str, Any]], None],
        request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ):
        self.notification, self.request = notification, request
        self.owner: LocalProcess | None = None
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.jobs: set[asyncio.Task[None]] = set()
        self.sequence = 0
        self.reader: asyncio.Task[None] | None = None
        self.stderr: asyncio.Task[None] | None = None
        self.closed = False
        self.failed = False
        self._write_lock = asyncio.Lock()

    async def start(self, command: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        try:
            self.owner = await LocalProcess.start(command, cwd=cwd, env=env, limit=MAX_FRAME + 1)
        except LocalLaunchCancelled as error:
            self.owner = error.owner
            raise
        if self.closed:
            await self.owner.close(timeout=2)
            raise NativeError("Native transport stopped during startup")
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain())

    async def send(self, message: dict[str, Any]) -> None:
        frame = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        limit = MAX_INPUT if message.get("method") in {"prompt", "turn/start"} else MAX_FRAME
        if len(frame) > limit or self.closed or not self.owner:
            raise NativeError("Native transport unavailable or request exceeds its bound")
        stream = self.owner.process.stdin
        assert stream
        async with self._write_lock:
            stream.write(frame)
            await stream.drain()

    async def call(
        self, method: str, params: dict[str, Any], timeout: float = 30
    ) -> dict[str, Any]:
        if self.failed or len(self.pending) >= 32:
            raise NativeError("Native transport unavailable")
        self.sequence += 1
        identity = self.sequence
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            await self.send({"id": identity, "method": method, "params": params})
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self.pending.pop(identity, None)

    async def _read(self) -> None:
        assert self.owner and self.owner.process.stdout
        messages = traffic = 0
        try:
            while frame := await self.owner.process.stdout.readline():
                messages += 1
                traffic += len(frame)
                if len(frame) > MAX_FRAME or traffic > 64 * 1024 * 1024 or messages > 100000:
                    raise NativeError("Native output exceeded its bound")
                value = json.loads(frame)
                if not isinstance(value, dict):
                    raise NativeError("Invalid native frame")
                if "method" in value:
                    params = value.get("params", {})
                    if not isinstance(params, dict):
                        raise NativeError("Invalid native parameters")
                    if "id" in value:
                        if len(self.jobs) >= 16:
                            raise NativeError("Too many native requests")
                        job = asyncio.create_task(
                            self._respond(value["id"], value["method"], params)
                        )
                        self.jobs.add(job)
                        job.add_done_callback(self.jobs.discard)
                    else:
                        self.notification(value["method"], params)
                elif type(value.get("id")) is int:
                    future = self.pending.get(value["id"])
                    if future and not future.done():
                        if "error" in value:
                            future.set_exception(NativeError("Native request failed"))
                        elif isinstance(value.get("result"), dict):
                            future.set_result(value["result"])
                        else:
                            future.set_exception(NativeError("Invalid native response"))
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            self.failed = True
            for job in self.jobs:
                job.cancel()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        NativeError("Native connection closed; nothing was replayed")
                    )
            self.notification("transport/closed", {})

    async def _respond(self, identity: Any, method: str, params: dict[str, Any]) -> None:
        try:
            result = await self.request(method, params)
            await self.send({"id": identity, "result": result})
        except (Exception, asyncio.CancelledError):
            with contextlib.suppress(Exception):
                await self.send(
                    {"id": identity, "error": {"code": -32603, "message": "Request unavailable"}}
                )

    async def _drain(self) -> None:
        assert self.owner and self.owner.process.stderr
        while await self.owner.process.stderr.read(8192):
            pass

    async def close(self) -> bool:
        self.closed = True
        if self.owner and self.owner.process.stdin:
            self.owner.process.stdin.close()
        exited = True
        if self.owner:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(2):
                    await self.owner.process.wait()
            exited = await self.owner.close(timeout=2)
        tasks = [task for task in [self.reader, self.stderr, *self.jobs] if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return exited
