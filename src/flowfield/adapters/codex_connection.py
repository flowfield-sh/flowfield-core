"""Native Codex user-scoped MCP configuration."""

import json
from typing import Any

from flowfield.adapters.connection import Connection
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessKind


class CodexConnection(Connection):
    kind: HarnessKind = "codex"
    label = "Codex"
    command = "codex"

    def matches(self, config: dict[str, Any]) -> bool:
        transport = config.get("transport", {})
        return (
            isinstance(transport, dict)
            and transport.get("type") == "streamable_http"
            and transport.get("url") == self.url
        )

    def configuration(self) -> dict[str, Any] | None:
        try:
            servers = json.loads(self.run("mcp", "list", "--json"))
            if not isinstance(servers, list) or any(not isinstance(s, dict) for s in servers):
                raise ValueError("Unexpected configuration")
            return next((s for s in servers if s.get("name") == self.name), None)
        except (ValueError, TypeError) as error:
            raise ApplicationError(
                "codex_response_invalid",
                "Cannot read Codex MCP configuration. Update Codex CLI and retry.",
            ) from error

    def add(self) -> None:
        self.run("mcp", "add", self.name, "--url", self.url)

    def remove(self) -> None:
        self.run("mcp", "remove", self.name)
