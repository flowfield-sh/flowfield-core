"""CLI listener ownership, startup errors and optional browser opening after readiness."""

import asyncio
import os
import socket
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn
from service_fixtures import running_service

from flowfield import serve


@pytest.mark.parametrize(
    "ready,interactive,enabled,expected",
    [(True, True, True, 1), (False, True, True, 0), (True, False, True, 0), (True, True, False, 0)],
)
def test_browser_starts_only_after_ready_once(
    monkeypatch, capsys, ready, interactive, enabled, expected
):
    events = []

    async def startup(server, sockets):
        events.append("ready")
        server.started = ready

    class Thread:
        def __init__(self, *, target, args, daemon):
            assert daemon
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    monkeypatch.setattr(serve.threading, "Thread", Thread)
    monkeypatch.setattr(serve.sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(serve.sys.stdout, "isatty", lambda: interactive)
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: events.append(url) or True)
    server = serve.BrowserServer(uvicorn.Config("unused", port=8773), open_browser=enabled)
    asyncio.run(server.startup())
    asyncio.run(server.startup())
    assert events.count("http://127.0.0.1:8773") == expected
    if expected:
        assert events[:2] == ["ready", "http://127.0.0.1:8773"]
    assert ("http://127.0.0.1:8773" in capsys.readouterr().out) == ready


def test_browser_failure_is_nonfatal_and_keeps_link(monkeypatch, capsys):
    def fail(url):
        raise OSError("No browser")

    monkeypatch.setattr(serve.webbrowser, "open", fail)
    serve.BrowserServer._open("http://127.0.0.1:8765")
    assert "http://127.0.0.1:8765" in capsys.readouterr().err


@pytest.mark.parametrize("fails", [False, True])
def test_serve_cli_no_open_and_selected_port(monkeypatch, fails):
    from typer.testing import CliRunner

    from flowfield.cli import app

    observed = []
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]

    def run(server, sockets):
        assert len(sockets) == 1
        assert sockets[0].getsockname() == ("127.0.0.1", port)
        observed.append(
            SimpleNamespace(
                port=server.config.port, open_browser=server.open_browser, socket=sockets[0]
            )
        )
        if fails:
            raise RuntimeError("Startup failed")

    monkeypatch.setattr(serve.BrowserServer, "run", run)
    result = CliRunner().invoke(app, ["serve", "--no-open", "--port", str(port)])
    assert result.exit_code == (1 if fails else 0), result.output
    if fails:
        assert isinstance(result.exception, RuntimeError)
    assert observed[0].port == port and not observed[0].open_browser
    assert observed[0].socket.fileno() == -1


@pytest.mark.parametrize("selection", ["command", "global", "environment"])
def test_occupied_port_exits_cleanly_before_opening_store(tmp_path, selection):
    state = tmp_path / "unopened"
    with socket.create_server(("127.0.0.1", 0)) as other:
        port = str(other.getsockname()[1])
        args = ["--data-dir", str(state)]
        env = {**os.environ, "FLOWFIELD_PORT": port}
        if selection == "command":
            env["FLOWFIELD_PORT"] = "1"  # Explicit serve option takes precedence.
            args += ["serve", "--port", port]
        elif selection == "global":
            env["FLOWFIELD_PORT"] = "1"
            args += ["--port", port, "serve"]
        else:
            args += ["serve"]
        result = subprocess.run(
            [sys.executable, "-m", "flowfield", *args, "--no-open"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 1, result.stderr
        assert result.stdout == ""
        assert result.stderr.startswith(f"Cannot start Flowfield: port {port} is already in use.")
        assert f"http://127.0.0.1:{port}" in result.stderr
        assert "flowfield serve --port PORT" in result.stderr
        assert "Traceback" not in result.stderr and "ERROR:" not in result.stderr
        assert not state.exists()
        assert other.fileno() != -1


def test_duplicate_serve_preserves_running_service_and_releases_port(tmp_path):
    state = tmp_path / "state"
    with running_service(state) as base:
        port = urlsplit(base).port
        for rejected_state in (state, tmp_path / "other-state"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "flowfield",
                    "--data-dir",
                    str(rejected_state),
                    "serve",
                    "--port",
                    str(port),
                    "--no-open",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode == 1, result.stderr
            assert result.stdout == ""
            assert result.stderr.startswith(
                f"Cannot start Flowfield: port {port} is already in use."
            )
            assert "Traceback" not in result.stderr and "locked" not in result.stderr.lower()
            assert httpx.get(base + "/api/health", trust_env=False).status_code == 200
        assert not (tmp_path / "other-state").exists()
    # Clean shutdown releases the exact port, including after refused second starts.
    with socket.create_server(("127.0.0.1", port)):
        pass
    with running_service(state) as base:
        assert httpx.get(base + "/api/health", trust_env=False).status_code == 200
