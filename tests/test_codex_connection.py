import json
import os
import shlex
import sys
from pathlib import Path

import anyio
import pytest
from test_connection import running_service
from typer.testing import CliRunner

from flowfield.adapters.connection import probe
from flowfield.cli import app
from flowfield.errors import ApplicationError


@pytest.fixture
def codex_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fixture = tmp_path / "codex-state.json"
    fixture.write_text(
        json.dumps(
            {
                "servers": {
                    "other": {
                        "name": "other",
                        "transport": {"type": "stdio", "command": "untouched"},
                    }
                },
                "writes": 0,
            }
        )
    )
    executable = tmp_path / "bin/codex"
    executable.parent.mkdir()
    script = Path(__file__).with_name("fake_codex.py")
    executable.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n'
    )
    executable.chmod(0o700)
    monkeypatch.setenv("FLOWFIELD_TEST_CODEX_STATE", str(fixture))
    monkeypatch.setenv("PATH", str(executable.parent) + os.pathsep + os.environ["PATH"])
    return fixture


def test_connect_check_disconnect_preserves_settings(tmp_path: Path, codex_config: Path) -> None:
    runner = CliRunner()
    original = json.loads(codex_config.read_text())["servers"]["other"]
    with running_service(tmp_path / "state") as base:
        port = base.rsplit(":", 1)[1]

        def command(operation: str, *args: str, success: bool = True) -> dict:
            result = runner.invoke(
                app,
                [
                    "--port",
                    port,
                    "integration",
                    operation,
                    "codex",
                    "--name",
                    "trial",
                    *args,
                    "--json",
                ],
            )
            assert result.exit_code == (0 if success else 1), result.output
            assert (result.stderr if success else result.stdout) == ""
            return json.loads(result.stdout if success else result.stderr)

        assert command("status", success=False)["error"]["code"] == "not_connected"
        connected = command("connect")
        assert connected["changed"] and connected["client"] == "not_observed"
        assert "get_task" in connected["tools"]
        assert not command("connect")["changed"]
        assert command("status")["service"] == "reachable"
        data = json.loads(codex_config.read_text())
        assert data["writes"] == 1 and data["servers"]["other"] == original
        data["servers"]["trial"]["enabled"] = False
        codex_config.write_text(json.dumps(data))
        assert command("connect")["enabled"] is False
        assert command("disconnect")["changed"]
        assert not command("disconnect")["changed"]
        data = json.loads(codex_config.read_text())
        assert data["writes"] == 2 and data["servers"] == {"other": original}


def test_conflicts_and_unavailable_service_never_change_config(
    tmp_path: Path, codex_config: Path
) -> None:
    data = json.loads(codex_config.read_text())
    data["servers"]["flowfield"] = {
        "name": "flowfield",
        "transport": {"type": "streamable_http", "url": "http://127.0.0.1:1/mcp/"},
    }
    codex_config.write_text(json.dumps(data))
    original = codex_config.read_bytes()
    for operation in ["connect", "status", "disconnect"]:
        result = CliRunner().invoke(app, ["integration", operation, "codex", "--json"])
        assert result.exit_code == 1
        assert json.loads(result.stderr)["error"]["code"] == "connection_conflict"
        assert codex_config.read_bytes() == original
    # Reserve then close a port, so the service is known to be absent.
    with running_service(tmp_path / "state") as base:
        port = base.rsplit(":", 1)[1]
    result = CliRunner().invoke(
        app, ["--port", port, "integration", "connect", "codex", "--name", "new", "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "mcp_unavailable"
    assert codex_config.read_bytes() == original


def test_prerequisites_and_private_diagnostics(
    codex_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_config.write_text(json.dumps({"fail": True}))
    result = CliRunner().invoke(app, ["integration", "connect", "codex", "--json"])
    assert result.exit_code == 1 and "private harness diagnostic" not in result.output
    assert json.loads(result.stderr)["error"]["code"] == "codex_command_failed"
    monkeypatch.setenv("PATH", "")
    result = CliRunner().invoke(app, ["integration", "connect", "codex", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "codex_missing"
    result = CliRunner().invoke(app, ["integration", "connect", "unknown", "--json"])
    assert json.loads(result.stderr)["error"]["code"] == "unsupported_harness"


def test_probe_rejects_a_non_mcp_service(tmp_path: Path) -> None:
    with running_service(tmp_path / "state") as base:
        with pytest.raises(ApplicationError, match="Cannot verify"):
            anyio.run(probe, base + "/api/health")
