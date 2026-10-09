"""Measured common ACP presentation and lifecycle for native harness adapters."""

import asyncio
import base64
import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable

from acp.schema import HttpMcpServer, ImageContentBlock, TextContentBlock
from pydantic import ValidationError

from flowfield.adapters.acp_session import AcpSession, AgentEvent, PermissionHandler
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessLaunch
from flowfield.run_activity import ActivityUpdate, ContextUsage


class AcpAgent(ABC):
    supports_activity = True

    def __init__(self, session: AcpSession):
        self.session = session
        self.launch: HarnessLaunch | None = None
        self.on_activity: Callable[[ActivityUpdate], None] | None = None
        self.cleanup_confirmed = True
        self.stopping = False
        self._text_key = 0
        self._last_tool = False

    @abstractmethod
    async def start(
        self, servers: list[HttpMcpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None: ...

    @abstractmethod
    async def configure(self, choice: AgentChoice) -> AgentChoice: ...

    @abstractmethod
    async def command_options(self) -> list[AgentCommand]: ...

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self.session.process

    def _event(self, event: AgentEvent) -> None:
        if self.on_activity is None:
            return
        if event.kind == "text":
            if self._last_tool:
                self._text_key += 1
            self._last_tool = False
            self.on_activity(
                ActivityUpdate(
                    key=f"agent-{self._text_key}",
                    kind="agent",
                    text=event.data["text"],
                    append=True,
                )
            )
        elif event.kind == "tool":
            self._last_tool = True
            data = event.data
            key = hashlib.sha256(str(data.get("toolCallId", "tool")).encode()).hexdigest()
            title = data.get("title") or "Tool"
            status = data.get("status")
            self.on_activity(
                ActivityUpdate(
                    key=key,
                    kind=(
                        "command"
                        if data.get("kind") == "execute" and not title.startswith("mcp.")
                        else "tool"
                    ),
                    text="\n".join(
                        filter(
                            None,
                            [
                                f"{title} · {status}" if status else title,
                                data.get("details"),
                            ],
                        )
                    ),
                )
            )
        elif event.kind == "usage":
            try:
                context = ContextUsage.model_validate(event.data)
            except ValidationError:
                return
            self.on_activity(ActivityUpdate(key="context", kind="status", text="", context=context))
        # ACP context occupancy is not billable input/output usage. Keep unsupported
        # token counters unknown rather than presenting context as consumed tokens.

    def validate_attachments(self, attachments: list[dict[str, str]]) -> None:
        if any(item["mime"].startswith("image/") for item in attachments) and not (
            self.session.capabilities.get("promptCapabilities", {}).get("image")
        ):
            raise ApplicationError(
                "images_unavailable",
                "This harness cannot receive images. Send text/code instead.",
                409,
            )

    async def command_prompt(
        self, text: str, *, has_history: bool, attachments: list[dict[str, str]]
    ) -> str | None:
        words = text.strip().split(maxsplit=1)
        if not words or not words[0].startswith("/"):
            return None
        name = words[0][1:]
        offered = next((item for item in await self.command_options() if item.name == name), None)
        if offered is None:
            raise ApplicationError(
                "command_unavailable",
                "This command is not available. Choose a command from the menu.",
                409,
            )
        if attachments or len(words) != 1:
            raise ApplicationError(
                "command_input", "This command does not accept attachments or arguments.", 409
            )
        if not has_history and name == "compact":
            raise ApplicationError(
                "command_session", "Send a message before compacting its conversation.", 409
            )
        return "/" + name

    async def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None,
        *,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        if self.stopping:
            return {"status": "stopped"}
        self.validate_attachments(attachments or [])
        self.session.on_permission = on_permission
        content: list[ImageContentBlock | TextContentBlock] = []
        for item in attachments or []:
            content.append(TextContentBlock(type="text", text=f"Human attachment: {item['name']}"))
            if item["mime"].startswith("image/"):
                content.append(
                    ImageContentBlock(type="image", data=item["data"], mime_type=item["mime"])
                )
            else:
                content.append(
                    TextContentBlock(
                        type="text", text=base64.b64decode(item["data"]).decode("utf-8")
                    )
                )
        outcome = await self.session.prompt(text, content=content)
        return {"status": "completed" if outcome == "end_turn" else outcome}

    async def stop(self) -> bool:
        self.stopping = True
        if self.session.state == "new":
            self.cleanup_confirmed = True
            return True
        receipt = await self.session.close()
        self.cleanup_confirmed = receipt.owned_work_stopped is True and receipt.process_group_exited
        return self.cleanup_confirmed

    async def close(self) -> None:
        await self.stop()
