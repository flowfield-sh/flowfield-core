"""Reusable isolated test support; importing this module never collects a test suite."""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx


@contextmanager
def running_service(state: Path) -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with (state.parent / "service.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "flowfield",
                "--data-dir",
                str(state),
                "serve",
                "--no-open",
                "--port",
                str(port),
            ],
            env={**os.environ},
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, "Service exited during startup"
                    try:
                        if client.get(base + "/api/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "Service startup timed out"
                    time.sleep(0.05)
            yield base
        except BaseException:
            log.seek(0)
            print(log.read())
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise AssertionError("Service did not shut down cleanly") from None
