"""Application-owned adapter contracts; native protocols stay in concrete adapters."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.harness_models import HarnessLaunch
from flowfield.run_activity import ActivityUpdate


class McpHeader(BaseModel):
    name: str
    value: str


class McpServer(BaseModel):
    type: str = "http"
    name: str
    url: str
    headers: list[McpHeader]

    def native(self) -> dict[str, Any]:
        return {
            "type": "http",
            "url": self.url,
            "headers": {item.name: item.value for item in self.headers},
        }


@dataclass(frozen=True)
class PermissionRequest:
    tool_id: str
    title: str
    options: tuple[tuple[str, str, str], ...]
    details: str = ""


PermissionHandler = Callable[[PermissionRequest], Coroutine[Any, Any, str | None]]


class Agent(ABC):
    supports_activity = True
    launch: HarnessLaunch | None = None
    session_id: str | None = None
    cleanup_confirmed = True
    stopping = False
    on_activity: Callable[[ActivityUpdate], None] | None = None

    @property
    @abstractmethod
    def process(self) -> asyncio.subprocess.Process | None: ...

    @abstractmethod
    async def start(
        self, servers: list[McpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None: ...

    @abstractmethod
    async def configure(self, choice: AgentChoice) -> AgentChoice: ...

    @abstractmethod
    async def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None,
        *,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, str]: ...

    @abstractmethod
    async def stop(self) -> bool: ...

    async def close(self) -> None:
        await self.stop()

    def validate_attachments(self, attachments: list[dict[str, str]]) -> None:
        # Both current native integrations accept current-turn text and images.
        return None

    async def command_options(self) -> list[AgentCommand]:
        return []

    async def command_prompt(
        self, text: str, *, has_history: bool, attachments: list[dict[str, str]]
    ) -> str | None:
        if not text.strip().startswith("/"):
            return None
        from flowfield.errors import ApplicationError

        command = text.strip()[1:]
        offered = {item.name for item in await self.command_options()}
        if command not in offered or attachments:
            raise ApplicationError(
                "command_unavailable", "Choose an available command from the menu.", 409
            )
        if command == "compact" and not has_history:
            raise ApplicationError(
                "command_session", "Send a message before compacting its conversation.", 409
            )
        return "/" + command

    def activity(self, value: ActivityUpdate) -> None:
        if self.on_activity:
            self.on_activity(value)


def bounded_details(value: str) -> str:
    from flowfield.run_activity import clean

    return clean(value)[:16000]
