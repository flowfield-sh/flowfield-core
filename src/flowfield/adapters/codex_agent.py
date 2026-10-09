"""Codex choices and public activity over ACP; native tools/policies stay in Codex."""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from acp.exceptions import RequestError
from acp.schema import HttpMcpServer, ImageContentBlock, TextContentBlock
from pydantic import BaseModel, ValidationError

from flowfield.adapters.acp_session import (
    AcpSession,
    AgentEvent,
    PermissionHandler,
    activity_locations,
    bounded_details,
    permission_details,
)
from flowfield.adapters.codex_cleanup import CODEX_SHUTDOWN_TIMEOUTS, quiesce, require_cleanup
from flowfield.adapters.codex_install import command
from flowfield.adapters.harness_host import launch_environment, resolve
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, NativeMode
from flowfield.harness_models import HarnessLaunch, HarnessRegistration
from flowfield.run_activity import ActivityUpdate, ContextUsage


def choices(config: list[dict[str, Any]], identity: str) -> list[dict[str, Any]]:
    option = next((item for item in config if item["id"] == identity), None)
    if option is None or option.get("type") != "select":
        return []
    return [entry for group in option.get("options", []) for entry in group.get("options", [group])]


def codex_permission_details(tool: BaseModel) -> str:
    """Allowlisted native request facts; exclude unrelated raw inputs and metadata."""
    data = tool.model_dump(by_alias=True, exclude_none=True)
    raw = data.get("rawInput")
    parts = [permission_details(tool)]
    if isinstance(raw, dict):
        for key in ("command", "cwd", "url", "additionalPermissions", "permissions"):
            if key in raw:
                value = raw[key]
                parts.append(f"{key}: {value if isinstance(value, str) else json.dumps(value)}")
    return bounded_details("\n\n".join(filter(None, parts)))


def codex_activity_details(tool: BaseModel) -> str:
    """Execution facts only; never prompts, arbitrary tool inputs or private reasoning."""
    data = tool.model_dump(by_alias=True, exclude_none=True)
    parts = [activity_locations(tool)]
    raw = data.get("rawInput")
    if isinstance(raw, dict):
        for key in ("command", "cwd", "path"):
            if isinstance(raw.get(key), str):
                parts.append(f"{key}: {raw[key]}")
    # MCP failures are reported in rawOutput by the bridge, without ACP text
    # content. Retain only the public error message, not arbitrary tool results.
    output = data.get("rawOutput")
    if data.get("status") == "failed" and isinstance(output, dict):
        error = output.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            parts.append(error["message"])
        result = output.get("result")
        if isinstance(result, dict):
            structured = result.get("structuredContent")
            if isinstance(structured, dict):
                error = structured.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    parts.append(error["message"])
    return bounded_details("\n".join(filter(None, parts)))


class CodexAgent:
    supports_activity = True

    def __init__(
        self,
        directory: Path,
        cwd: Path,
        environment: Mapping[str, str],
        *,
        registration: HarnessRegistration | None = None,
    ):
        self.launch: HarnessLaunch | None = None
        if registration is not None:
            if registration.harness != "codex":
                raise ValueError("Codex requires a Codex registration")
            self.launch = resolve(registration, environment)
            environment = launch_environment(self.launch, environment)
        self.command, self.environment = command(directory, environment)
        if self.launch:
            from flowfield.adapters.codex_install import VERSION

            self.launch.bridge_executable = self.command[0]
            self.launch.bridge_version = VERSION
        self.cwd = cwd
        self.on_activity: Callable[[ActivityUpdate], None] | None = None
        self.session = AcpSession(
            self._event,
            cleanup=quiesce,
            shutdown_timeouts=CODEX_SHUTDOWN_TIMEOUTS,
            permission_projection=codex_permission_details,
            activity_projection=codex_activity_details,
        )
        self.cleanup_confirmed = True
        self.stopping = False
        self._text_key = 0
        self._last_tool = False

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

    async def start(
        self, servers: list[HttpMcpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None:
        self.cleanup_confirmed = False
        try:
            await self.session.start(
                self.command,
                cwd=self.cwd,
                env=self.environment,
                mcp_servers=servers,
                resume_session_id=resume,
                require_resume=persistent,
            )
        except RequestError as error:
            if resume:
                raise ApplicationError(
                    "agent_resume_failed",
                    "The saved agent session could not be resumed. Check Codex on the host "
                    "and retry, or explicitly start a new session. Nothing was replayed.",
                    409,
                ) from error
            if error.code == -32000:
                raise ApplicationError(
                    "codex_login_required",
                    "Codex requires authentication. Sign in with Codex on the service host, "
                    "then reload available models.",
                    409,
                ) from error
            raise ApplicationError(
                "codex_start_failed",
                "Codex could not open an ACP session. Check its native configuration and "
                "the installed bridge before retrying.",
                409,
            ) from error
        except (OSError, RuntimeError, TimeoutError) as error:
            if resume:
                raise ApplicationError(
                    "agent_resume_failed",
                    "The saved agent session could not be resumed. Check Codex on the host "
                    "and retry, or explicitly start a new session. Nothing was replayed.",
                    409,
                ) from error
            raise ApplicationError(
                "codex_start_failed",
                "Codex could not open an ACP session. Check the service PATH, native "
                "configuration and installed bridge before retrying.",
                409,
            ) from error
        require_cleanup(self.session.capabilities)

    async def configure(self, choice: AgentChoice) -> AgentChoice:
        if self.stopping:
            raise ApplicationError("agent_stopping", "The agent is stopping.", 409)
        if not choice.mode:
            raise ApplicationError(
                "agent_mode_required", "Choose an Access mode in agent settings.", 409
            )
        try:
            await self.session.select("mode", choice.mode)
            await self.session.select("model", choice.model)
            await self.session.select("reasoning_effort", choice.effort)
            expected = {
                "model": choice.model,
                "reasoning_effort": choice.effort,
                "mode": choice.mode,
            }
            if choice.fast is not None:
                available = {item["value"] for item in choices(self.session.config, "fast-mode")}
                if {"on", "off"} <= available:
                    fast_value = "on" if choice.fast else "off"
                    expected["fast-mode"] = fast_value
                    await self.session.select("fast-mode", fast_value)
                elif choice.fast:
                    raise ValueError("Fast mode is unavailable for this model")
            current = {item["id"]: item.get("currentValue") for item in self.session.config}
            if any(current.get(key) != value for key, value in expected.items()):
                raise RuntimeError("Native settings changed while configuring")
            return choice.model_copy(update={"mode": expected["mode"]})
        except (ValueError, RuntimeError, RequestError) as error:
            raise ApplicationError(
                "agent_choice_unavailable",
                "Codex could not apply the saved model, effort, access or fast mode. "
                "Reload available choices and save settings.",
                409,
            ) from error

    def validate_attachments(self, attachments: list[dict[str, str]]) -> None:
        if any(item["mime"].startswith("image/") for item in attachments) and not (
            self.session.capabilities.get("promptCapabilities", {}).get("image")
        ):
            raise ApplicationError(
                "images_unavailable",
                "This harness cannot receive images. Send text/code instead.",
                409,
            )

    async def command_options(self) -> list[AgentCommand]:
        try:
            await asyncio.wait_for(self.session.commands_received.wait(), 10)
        except TimeoutError as error:
            raise ApplicationError(
                "commands_unavailable",
                "Codex did not return its commands. Try reloading them.",
                409,
            ) from error
        supported = {"compact", "status", "mcp", "skills"}
        return [item for item in self.session.commands if item.name in supported]

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


async def model_options(
    directory: Path, *, registration: HarnessRegistration | None = None
) -> list[ModelOption]:
    # Discovery opens a disposable native session, never a model turn. Bound total
    # work because model-dependent effort options require selecting each model.
    with tempfile.TemporaryDirectory(prefix="flowfield-catalog-") as temporary:
        agent = CodexAgent(
            directory, Path(temporary).resolve(), os.environ, registration=registration
        )
        try:
            async with asyncio.timeout(120):
                await agent.start([])
                catalog = choices(agent.session.config, "model")
                if len(catalog) > 64:
                    raise ApplicationError(
                        "agent_catalog_limit", "Codex returned too many models.", 409
                    )
                modes = [
                    NativeMode(
                        id=item["value"],
                        name=item["name"],
                        description=item.get("description") or "",
                    )
                    for item in choices(agent.session.config, "mode")
                ]
                result = []
                for item in catalog:
                    await agent.session.select("model", item["value"])
                    result.append(
                        ModelOption(
                            id=item["value"],
                            name=item["name"],
                            efforts=[
                                c["value"]
                                for c in choices(agent.session.config, "reasoning_effort")
                            ],
                            modes=modes,
                            fast={"on", "off"}
                            <= {c["value"] for c in choices(agent.session.config, "fast-mode")},
                            fast_description=next(
                                (
                                    c.get("description") or ""
                                    for c in agent.session.config
                                    if c["id"] == "fast-mode"
                                ),
                                "",
                            )[:1000],
                        )
                    )
                return result
        finally:
            await agent.close()


async def command_options(
    directory: Path,
    cwd: Path,
    choice: AgentChoice,
    *,
    registration: HarnessRegistration | None = None,
) -> list[AgentCommand]:
    agent = CodexAgent(directory, cwd, os.environ, registration=registration)
    try:
        async with asyncio.timeout(60):
            await agent.start([])
            await agent.configure(choice)
            return await agent.command_options()
    finally:
        await agent.close()
