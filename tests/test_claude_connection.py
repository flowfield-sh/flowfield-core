"""Standalone setup shares the CLI journey while preserving native user settings."""

import json
from pathlib import Path

import pytest
from test_connection import running_service
from typer.testing import CliRunner

from flowfield.adapters.claude_connection import ClaudeConnection
from flowfield.cli import app


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps({"mcpServers": {"other": {"type": "stdio", "command": "keep"}}, "theme": "dark"})
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("flowfield.adapters.connection.shutil.which", lambda _: "/fake/claude")

    def run(self, *args):
        data = json.loads(path.read_text())
        if args[:2] == ("mcp", "add"):
            assert args[2:] == ("--scope", "user", "--transport", "http", self.name, self.url)
            assert self.name not in data["mcpServers"]
            data["mcpServers"][self.name] = {"type": "http", "url": self.url}
        else:
            assert args == ("mcp", "remove", "--scope", "user", self.name)
            del data["mcpServers"][self.name]
        path.write_text(json.dumps(data))
        return ""

    monkeypatch.setattr(ClaudeConnection, "run", run)
    return path


def test_shared_connect_status_disconnect(tmp_path: Path, settings: Path) -> None:
    original = settings.read_bytes()
    with running_service(tmp_path / "state") as base:

        def command(operation, *, human=False):
            result = CliRunner().invoke(
                app,
                [
                    "--port",
                    base.rsplit(":", 1)[1],
                    "integration",
                    operation,
                    "claude-code",
                    "--name",
                    "trial",
                    *([] if human else ["--json"]),
                ],
            )
            assert result.exit_code == 0, result.output
            return result.stdout if human else json.loads(result.stdout)

        connected = command("connect")
        assert connected["changed"] and connected["harness"] == "claude-code"
        assert connected["client"] == "not_observed" and connected["enabled"] is None
        assert "get_task" in connected["tools"]
        saved = settings.read_bytes()
        assert not command("connect")["changed"]
        assert command("status")["service"] == "reachable"
        assert settings.read_bytes() == saved
        assert "Codex" not in command("status", human=True)
        assert "MCP server: trial" in command("status", human=True)
        assert command("disconnect")["changed"]
        assert not command("disconnect")["changed"]
        assert settings.read_bytes() == original


@pytest.mark.parametrize(
    "config",
    [{"type": "http", "url": "http://elsewhere/mcp/"}, {"type": "stdio", "command": "keep"}],
)
def test_connection_conflicts_preserve_user_config(settings: Path, config: dict) -> None:
    data = json.loads(settings.read_text())
    data["mcpServers"]["flowfield"] = config
    settings.write_text(json.dumps(data))
    original = settings.read_bytes()
    for operation in ("connect", "status", "disconnect"):
        result = CliRunner().invoke(app, ["integration", operation, "claude-code", "--json"])
        assert result.exit_code == 1
        assert json.loads(result.stderr)["error"]["code"] == "connection_conflict"
        assert settings.read_bytes() == original


@pytest.mark.parametrize(
    "content",
    ["private invalid json", "[]", '{"mcpServers": []}', '{"mcpServers": {"flowfield": 1}}'],
)
def test_unreadable_config_never_overwrites_or_leaks(settings: Path, content: str) -> None:
    settings.write_text(content)
    result = CliRunner().invoke(app, ["integration", "connect", "claude-code", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "claude_response_invalid"
    assert "private invalid json" not in result.output
    assert settings.read_text() == content


def test_absent_service_never_writes(tmp_path: Path, settings: Path) -> None:
    with running_service(tmp_path / "state") as base:
        port = base.rsplit(":", 1)[1]
    original = settings.read_bytes()
    result = CliRunner().invoke(
        app, ["--port", port, "integration", "connect", "claude-code", "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "mcp_unavailable"
    assert settings.read_bytes() == original
