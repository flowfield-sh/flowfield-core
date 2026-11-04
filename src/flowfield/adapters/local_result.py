"""Concrete Local result execution. No task scheduling, result selection or delivery authority."""

import asyncio
import codecs
import contextlib
import json
import os
import socket
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from flowfield.adapters import result_process
from flowfield.adapters.git_workspace import git
from flowfield.adapters.local_execution import FileWorkspace, LocalAttempt, LocalHost
from flowfield.adapters.local_process import LocalProcess


class LocalResultEnvironment:
    @staticmethod
    def prepare_copy(directory: Path, repository: Path, identity: str, commit: str) -> LocalAttempt:
        return LocalHost(os.environ, cache_root=directory.parent / "setup-cache").prepare(
            directory, repository, identity, commit
        )

    def __init__(
        self,
        identity: str,
        checkout: Path,
        generation: str,
        output: Callable[[str], None],
        *,
        cache_root: Path | None = None,
    ):
        self.runtime = checkout.parent / "runtime"
        common = Path(
            git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")
            .decode()
            .strip()
        )
        self.environment = LocalHost(os.environ, cache_root=cache_root).restore(
            identity, FileWorkspace(checkout.parent, checkout, common), self.runtime
        )
        self.generation = generation
        self.output = output
        self.owners: list[LocalProcess] = []
        self.receipts: list[Path] = []
        self.log = self.runtime / f"runtime-{generation}.log"
        self.log_bytes = 0
        self.log_truncated = False
        # Some tools use Unix sockets under TMPDIR; long checkout paths exceed OS limits.
        # Only this private alias is outside retained state. Its target stays copy-owned.
        self.temporary = Path(tempfile.mkdtemp(prefix="ff-r-", dir="/tmp"))
        self.port_lease = socket.socket()
        self.temporary_receipt = self.runtime / f"temporary-{generation}.json"
        try:
            (self.temporary / "t").symlink_to(self.runtime / "tmp", target_is_directory=True)
            self.temporary_receipt.write_text(json.dumps(str(self.temporary)))
            self.port_lease.bind(("127.0.0.1", 0))
        except OSError:
            self.port_lease.close()
            (self.temporary / "t").unlink(missing_ok=True)
            self.temporary.rmdir()
            self.temporary_receipt.unlink(missing_ok=True)
            raise
        self.port = self.port_lease.getsockname()[1]

    @staticmethod
    def recover_temporary(runtime: Path) -> None:
        """Remove only recorded aliases whose process receipts already confirm cleanup."""
        if any(not LocalResultEnvironment.clean(p) for p in runtime.glob("owner-*.json")):
            return
        for receipt in runtime.glob("temporary-*.json"):
            try:
                alias = Path(json.loads(receipt.read_text()))
                if alias.parent != Path("/tmp") or not alias.name.startswith("ff-r-"):
                    continue
                link = alias / "t"
                if link.is_symlink() and link.readlink() == runtime / "tmp":
                    link.unlink()
                    alias.rmdir()  # Never recursively delete unrecognized contents.
                if not alias.exists():
                    receipt.unlink()
            except (OSError, ValueError, TypeError):
                continue

    def resolve_url(self, url: str) -> str:
        return url.replace("${FLOWFIELD_PORT}", str(self.port)).replace(
            "$FLOWFIELD_PORT", str(self.port)
        )

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
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            with self.log.open("ab") as log:
                while block := await stream.read(8192):
                    retained = block[: max(0, 8 * 1024 * 1024 - self.log_bytes)]
                    log.write(retained)
                    log.flush()
                    self.log_bytes += len(retained)
                    self.log_truncated |= len(retained) < len(block)
                    self.output(decoder.decode(block))
                self.output(decoder.decode(b"", final=True))

        try:
            variables = self.environment.launch_environment()
            variables.update(
                TMPDIR=str(self.temporary / "t"),
                TMP=str(self.temporary / "t"),
                TEMP=str(self.temporary / "t"),
                FLOWFIELD_PORT=str(self.port),
            )
            # Release immediately before launch. Programs must bind this port themselves;
            # a bind failure remains a startup failure, never authority to kill its owner.
            self.port_lease.close()
            launching = asyncio.create_task(
                LocalProcess.start(
                    [
                        sys.executable,
                        str(Path(result_process.__file__).resolve()),
                        str(receipt),
                        command,
                    ],
                    cwd=self.environment.checkout,
                    env=variables,
                )
            )
            try:
                owner = await asyncio.shield(launching)
            except asyncio.CancelledError:
                # Let the guardian adopt its child and observe EOF before killing
                # its group. Cancelling a not-yet-started readiness probe otherwise
                # kills the guardian before it can write its cleanup receipt.
                while True:
                    try:
                        owner = await asyncio.shield(launching)
                        break
                    except asyncio.CancelledError:
                        if launching.cancelled():
                            raise
                raise
            assert owner.process.stdout and owner.process.stderr
            readers = [
                asyncio.create_task(drain(stream))
                for stream in (owner.process.stdout, owner.process.stderr)
            ]
            return await owner.process.wait()
        except OSError:
            if owner is None:
                receipt.write_text('{"cleanup_confirmed": true}')
            raise
        finally:
            if owner:
                self.owners.append(owner)
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
        clean = all(item is True for item in cleanup) and all(
            self.clean(path) for path in self.receipts
        )
        self.port_lease.close()
        if clean:
            try:
                (self.temporary / "t").unlink(missing_ok=True)
                self.temporary.rmdir()
                self.temporary_receipt.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            except OSError:
                clean = False
        return clean
