"""Pi's native user-scoped MCP setup through the shared connection lifecycle."""

import os
from pathlib import Path
from typing import Any

from flowfield.adapters.connection import Connection
from flowfield.harness_models import HarnessKind


class PiConnection(Connection):
    kind: HarnessKind = "pi"
    label = "Pi"
    command = "pi"

    def configuration(self) -> dict[str, Any] | None:
        directory = Path(os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent")))
        return self.read_json_configuration(directory.expanduser() / "mcp.json")

    def matches(self, config: dict[str, Any]) -> bool:
        return (
            config.get("type") in (None, "http", "streamable-http")
            and not config.get("command")
            and config.get("url") == self.url
        )

    def add(self) -> None:
        self.run("mcp", "add", self.name, "--url", self.url, "--exposure", "direct")

    def remove(self) -> None:
        self.run("mcp", "remove", self.name)
