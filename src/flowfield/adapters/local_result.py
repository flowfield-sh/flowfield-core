"""Concrete Local result execution. No task scheduling, result selection or delivery authority."""

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from flowfield.adapters import result_process
from flowfield.adapters.local_execution import FileWorkspace, LocalHost
from flowfield.adapters.local_process import LocalLaunchCancelled, LocalProcess


class LocalResultEnvironment:
    def __init__(
        self, identity: str, checkout: Path, generation: str, output: Callable[[bytes], None]
    ):
        self.runtime = checkout.parent / "runtime"
        self.environment = LocalHost(os.environ).restore(
            identity, FileWorkspace(checkout.parent, checkout), self.runtime
        )
        self.generation = generation
        self.output = output
        self.owners: list[LocalProcess] = []
        self.receipts: list[Path] = []
        self.log = self.runtime / f"runtime-{generation}.log"
        self.log_bytes = 0
        self.log_truncated = False

    @staticmethod
    def clean(receipt: Path) -> bool:
        try:
            return json.loads(receipt.read_text()).get("cleanup_confirmed") is True
        except (OSError, ValueError):
            return False

    async def run(self, command: str) -> int:
        receipt = self.runtime / f"owner-{self.generation}-{uuid4().hex}.json"
        receipt.write_text('{"cleanup_confirmed": false}')
        self.receipts.append(receipt)
        owner: LocalProcess | None = None
        readers: list[asyncio.Task[None]] = []

        async def drain(stream: asyncio.StreamReader) -> None:
            with self.log.open("ab") as log:
                while block := await stream.read(8192):
                    retained = block[: max(0, 8 * 1024 * 1024 - self.log_bytes)]
                    log.write(retained)
                    log.flush()
                    self.log_bytes += len(retained)
                    self.log_truncated |= len(retained) < len(block)
                    self.output(block)

        try:
            owner = await LocalProcess.start(
                [
                    sys.executable,
                    str(Path(result_process.__file__).resolve()),
                    str(receipt),
                    command,
                ],
                cwd=self.environment.checkout,
                env=self.environment.launch_environment(),
            )
            self.owners.append(owner)
            assert owner.process.stdout and owner.process.stderr
            readers = [
                asyncio.create_task(drain(stream))
                for stream in (owner.process.stdout, owner.process.stderr)
            ]
            return await owner.process.wait()
        except LocalLaunchCancelled as error:
            owner = error.owner
            self.owners.append(owner)
            raise
        except OSError:
            if owner is None:
                receipt.write_text('{"cleanup_confirmed": true}')
            raise
        finally:
            if owner:
                if owner.process.stdin:
                    owner.process.stdin.close()
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(5):
                        await owner.process.wait()
                await owner.close()
            for reader in readers:
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(1):
                        await asyncio.shield(reader)
                if not reader.done():
                    reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    async def close(self) -> bool:
        cleanup = await asyncio.gather(
            *(owner.close() for owner in self.owners), return_exceptions=True
        )
        return all(item is True for item in cleanup) and all(
            self.clean(path) for path in self.receipts
        )
