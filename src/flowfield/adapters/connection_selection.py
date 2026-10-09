"""Select a supported standalone connection without a fallback harness."""

from flowfield.adapters.claude_connection import ClaudeConnection
from flowfield.adapters.codex_connection import CodexConnection
from flowfield.adapters.connection import Connection
from flowfield.adapters.pi_connection import PiConnection
from flowfield.errors import ApplicationError

CONNECTIONS: dict[str, type[Connection]] = {
    "codex": CodexConnection,
    "claude-code": ClaudeConnection,
    "pi": PiConnection,
}


def connection(harness: str, port: int, name: str) -> Connection:
    factory = CONNECTIONS.get(harness)
    if factory is None:
        raise ApplicationError("unsupported_harness", "This harness is not supported.")
    return factory(port, name)
