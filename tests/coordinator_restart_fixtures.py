"""Owned HTTP service processes and a durable, model-free native peer."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx


@contextmanager
def running_service(directory, native_state, log_path):
    with socket.socket() as listener, log_path.open("w+") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, __file__, str(directory), str(listener.fileno())],
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "FLOWFIELD_UPDATE_CHECKS": "0",
                "FLOWFIELD_TEST_NATIVE_STATE": str(native_state),
                "CODEX_PATH": sys.executable,
                "CODEX_HOME": str(native_state),
            },
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1) as client:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        if client.get("/api/health").is_success:
                            break
                    except httpx.TransportError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        raise AssertionError(f"Service startup failed:\n{log.read()}")
                    time.sleep(0.02)
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise AssertionError("Owned service did not stop gracefully") from None
            log.seek(0)
            output = log.read()
            # Uvicorn re-raises captured signals after completing lifespan cleanup.
            assert process.returncode in (0, -signal.SIGTERM), output
            assert "TEST_SERVICE_STOPPED" in output, output


def methods(native_state):
    path = native_state / "requests.jsonl"
    return (
        [json.loads(line)["method"] for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def completed(client, request):
    response = client.post("/api/workspaces/harbor/coordinator/messages", json=request)
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 15
    while True:
        response = client.get("/api/workspaces/harbor/coordinator")
        response.raise_for_status()
        turn = next(item for item in response.json()["items"] if item["id"] == request["id"])
        if turn["status"] not in {"starting", "running", "stopping"}:
            return turn
        assert time.monotonic() < deadline, turn
        time.sleep(0.02)


if __name__ == "__main__":
    import uvicorn

    from flowfield.adapters import codex_agent
    from flowfield.api import create_app

    codex_agent.command = lambda directory, env: (
        [
            sys.executable,
            str(Path(__file__).with_name("fake_native_codex.py")),
            "managed",
            "coordinator",
            "cleanup",
            "close-session",
        ],
        dict(env),
    )
    app = create_app(data_dir=Path(sys.argv[1]))
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def observed_lifespan(app):
        async with lifespan(app):
            yield
        print("TEST_SERVICE_STOPPED", flush=True)

    app.router.lifespan_context = observed_lifespan
    uvicorn.run(app, fd=int(sys.argv[2]), access_log=False)
