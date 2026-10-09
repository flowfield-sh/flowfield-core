"""Claude's user-scoped MCP settings, written only through its native CLI."""

import json
import os
from pathlib import Path
from typing import Any

from flowfield.adapters.connection import Connection
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessKind


class ClaudeConnection(Connection):
    kind: HarnessKind = "claude-code"
    label = "Claude Code"
    command = "claude"

    def configuration(self) -> dict[str, Any] | None:
        # Claude has no machine-readable MCP get/list command. Read only its
        # documented user layer; leave all writes/locking/backups to the CLI.
        directory = os.environ.get("CLAUDE_CONFIG_DIR")
        path = (Path(directory).expanduser() if directory else Path.home()) / ".claude.json"
        try:
            data = json.loads(path.read_text())
            servers = data.get("mcpServers", {})
            if not isinstance(servers, dict):
                raise ValueError("Invalid server map")
            config = servers.get(self.name)
            if config is not None and not isinstance(config, dict):
                raise ValueError("Invalid server")
            return config
        except FileNotFoundError:
            return None
        except (OSError, ValueError, AttributeError) as error:
            raise ApplicationError(
                "claude_response_invalid",
                "Cannot read Claude Code MCP configuration. Run claude mcp list to diagnose.",
            ) from error

    def matches(self, config: dict[str, Any]) -> bool:
        return config.get("type") in ("http", "streamable-http") and config.get("url") == self.url

    def add(self) -> None:
        self.run("mcp", "add", "--scope", "user", "--transport", "http", self.name, self.url)

    def remove(self) -> None:
        self.run("mcp", "remove", "--scope", "user", self.name)
