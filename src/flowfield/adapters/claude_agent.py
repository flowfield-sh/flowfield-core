"""Official Claude Agent SDK running the user's installed Claude Code."""

import asyncio
import base64
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from flowfield.activity_text import command_title
from flowfield.adapters.agent_contract import (
    COMMANDS,
    Agent,
    McpServer,
    PermissionHandler,
    PermissionRequest,
    bounded_details,
)
from flowfield.adapters.claude_runtime import ADAPTER_VERSION, executable
from flowfield.adapters.harness_host import launch_environment, resolve
from flowfield.adapters.json_rpc import MAX_INPUT, JsonRpc, NativeError
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, NativeMode
from flowfield.harness_models import HarnessRegistration
from flowfield.run_activity import ActivityUpdate, ContextUsage

MODES = {"default", "acceptEdits", "auto", "bypassPermissions"}


def tool_title(name: str) -> str:
    """Present Claude's scoped MCP identifier without the per-session server suffix."""
    match = re.fullmatch(r"mcp__flowfield(?:_[a-f0-9]{16}|_[a-f0-9]{32})?__(\w+)", name)
    if match:
        return ("Flowfield · " + match[1].replace("_", " ").capitalize())[:4000]
    return name[:4000]


def model_name(name: str, resolved: str) -> str:
    # Native display names can be bare aliases. Keep the verified version visible.
    match = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)(?:-\d{8})?", resolved)
    if match:
        version = f"{match[2]}.{match[3]}"
        return name if version in name else f"{match[1].capitalize()} {version}"
    return name if resolved in name else f"{name} ({resolved})"


class CleanupReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: StrictInt = Field(ge=1, le=1)
    method: Literal["stop"]
    scope: Literal["native-turns-and-tasks"]
    sessionId: str = Field(min_length=1, max_length=500)
    status: Literal["confirmed", "uncertain"]
    reason: str | None = Field(max_length=100)
    checkedNativeOwners: StrictInt = Field(ge=0, le=1)
    stoppedTasks: StrictInt = Field(ge=0, le=256)
    quietObservations: StrictInt = Field(ge=0, le=100)
    nativeOwnerExited: StrictBool


class ClaudeAgent(Agent):
    # JSON escaping can expand text sixfold; leave framing to the native adapter.
    max_prompt_bytes = MAX_INPUT // 6

    def __init__(
        self,
        cwd: Path,
        environment: Mapping[str, str],
        *,
        registration: HarnessRegistration,
        choice: AgentChoice | None = None,
    ):
        if registration.harness != "claude-code":
            raise ValueError("Claude requires a Claude registration")
        if choice and (choice.harness != "claude-code" or choice.mode not in MODES):
            raise ApplicationError("agent_mode_required", "Choose a native access mode.", 409)
        self.launch = resolve(registration, environment)
        self.launch.adapter_version = ADAPTER_VERSION
        self.launch.runtime_executable = str(executable())
        self.environment = launch_environment(self.launch, environment)
        self.environment.pop("CLAUDE_AGENT_LOGS", None)
        self.cwd = cwd
        self.choice = choice.model_copy(deep=True) if choice else None
        self.rpc = JsonRpc(self._event, self._request)
        self.info: dict[str, Any] = {}
        self._permission: PermissionHandler | None = None
        self._configured = False
        self._running = False
        self._tools: dict[str, dict[str, Any]] = {}
        self._close_task: asyncio.Task[bool] | None = None
        self._startup_lock = asyncio.Lock()
        self.stopping = False
        self.session_id = None
        self.cleanup_confirmed = True

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self.rpc.owner.process if self.rpc.owner else None

    async def start(
        self, servers: list[McpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None:
        async with self._startup_lock:
            if self.stopping:
                raise NativeError("Agent stopped before startup")
            runtime = self.launch.runtime_executable if self.launch else None
            assert runtime
            self.cleanup_confirmed = False
            await self.rpc.start([str(runtime)], self.cwd, self.environment)
            try:
                self.info = await self.rpc.call(
                    "start",
                    {
                        "cwd": str(self.cwd),
                        "executable": self.launch.native_executable if self.launch else None,
                        "resume": resume,
                        "persistent": persistent,
                        "model": self.choice.model if self.choice else None,
                        "mode": self.choice.mode if self.choice else "default",
                        "effort": self.choice.effort if self.choice else None,
                        "servers": {server.name: server.native() for server in servers},
                    },
                )
                identity = self.info.get("sessionId")
                if (
                    not isinstance(identity, str)
                    or not identity
                    or len(identity) > 200
                    or (resume and identity != resume)
                ):
                    raise NativeError("Native session identity unconfirmed")
                self.session_id = identity
                headroom = self.info.get("inputTokensAvailable")
                self.input_tokens_available = (
                    headroom if type(headroom) is int and headroom >= 0 else None
                )
                if self.choice:
                    self._verify_model(self.choice)
            except (NativeError, OSError, TimeoutError) as error:
                raise ApplicationError(
                    "agent_resume_failed" if resume else "claude_start_failed",
                    "Claude could not open the requested session or access mode. "
                    "Check native login/settings and refresh models. Nothing was replayed.",
                    409,
                ) from error

    def _verify_model(self, choice: AgentChoice) -> None:
        if self.info.get("model") != choice.model or not any(
            m.get("resolvedModel") == choice.model for m in self.info.get("models", [])
        ):
            raise ApplicationError(
                "agent_model_unconfirmed",
                "Claude did not confirm the requested model. Refresh native choices. "
                "No prompt was sent.",
                409,
            )

    async def configure(self, choice: AgentChoice) -> AgentChoice:
        if self.stopping or not self.session_id or not self.choice or choice != self.choice:
            raise ApplicationError(
                "agent_choice_unavailable",
                "Claude needs a fresh session for changed settings.",
                409,
            )
        self._verify_model(choice)
        model = next(m for m in self.info["models"] if m.get("resolvedModel") == choice.model)
        if (
            choice.mode not in MODES
            or (
                choice.mode == "auto"
                and not any(
                    item.get("resolvedModel") == choice.model and item.get("autoMode") is True
                    for item in self.info["models"]
                )
            )
            or choice.fast
            or (
                choice.effort not in model["efforts"]
                if model["efforts"]
                else choice.effort is not None
            )
        ):
            raise ApplicationError(
                "agent_choice_unavailable",
                "The requested native effort or access mode is unavailable.",
                409,
            )
        try:
            # Confirm policy/version acceptance before any prompt, including resumes.
            await self.rpc.call("selectMode", {"sessionId": self.session_id, "mode": choice.mode})
        except NativeError as error:
            raise ApplicationError(
                "agent_choice_unavailable",
                "Claude did not accept this access mode. Refresh models or choose another mode. "
                "No prompt was sent.",
                409,
            ) from error
        self._configured = True
        return choice.model_copy(deep=True)

    async def command_options(self) -> list[AgentCommand]:
        available = await self.rpc.call("commands", {"sessionId": self.session_id})
        return [
            item.model_copy()
            for item in COMMANDS
            if item.name != "compact" or available.get("compact") is True
        ]

    async def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None,
        *,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        if self.stopping:
            return {"status": "stopped"}
        if not self._configured or not self.choice or self._running:
            raise NativeError("Apply native settings before dispatch")
        if text in {"/status", "/mcp", "/skills"}:
            response = await self.rpc.call(
                "command", {"sessionId": self.session_id, "name": text[1:]}
            )
            self.activity(
                ActivityUpdate(
                    key="command",
                    kind="agent",
                    text=bounded_details(str(response.get("text") or "None available.")),
                )
            )
            return {"status": "stopped" if self.stopping else "completed"}
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for item in attachments or []:
            content.append({"type": "text", "text": "Human attachment: " + item["name"]})
            if item["mime"].startswith("image/"):
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": item["mime"],
                            "data": item["data"],
                        },
                    }
                )
            else:
                content.append(
                    {"type": "text", "text": base64.b64decode(item["data"]).decode("utf-8")}
                )
        self._running = True
        self._permission = on_permission
        try:
            # Long turns have application-owned cancellation; no transport replay.
            response = await self.rpc.call(
                "prompt",
                {"sessionId": self.session_id, "content": content, "model": self.choice.model},
                timeout=3600,
            )
            if response.get("status") not in {"completed", "stopped"}:
                raise NativeError("Native turn failed")
            return {"status": response["status"]}
        finally:
            self._running = False
            self._permission = None

    def _event(self, method: str, data: dict[str, Any]) -> None:
        if (
            method != "activity"
            or data.get("sessionId") != self.session_id
            or self.stopping
            or not self._running
        ):
            return
        kind = data.get("kind")
        if kind == "text" and isinstance(data.get("text"), str):
            self.activity(
                ActivityUpdate(
                    key=str(data.get("key", "agent"))[:100],
                    kind="agent",
                    text=data["text"],
                    append=True,
                )
            )
        elif kind == "tool":
            identity = str(data.get("key", "tool"))[:100]
            tool = self._tools.setdefault(identity, {})
            tool.update({key: data[key] for key in ("title", "details", "status") if key in data})
            while len(self._tools) > 100:
                del self._tools[next(iter(self._tools))]
            title = tool.get("title", "Tool")
            details = bounded_details(
                "\n".join(f"{key}: {value}" for key, value in tool.get("details", {}).items())
            )
            label = (
                command_title(str(tool.get("details", {}).get("command", "Run command")))
                if title == "Bash"
                else tool_title(title)
            )
            self.activity(
                ActivityUpdate(
                    key=identity,
                    kind="command" if title == "Bash" else "tool",
                    text=f"{label} · {tool.get('status', 'running')}\n{details}",
                )
            )
        elif kind == "usage":
            used, size = data.get("used"), data.get("size")
            if type(used) is int and used >= 0 and type(size) is int and size > 0:
                self.activity(
                    ActivityUpdate(
                        key="context",
                        kind="status",
                        text="",
                        context=ContextUsage(used=used, size=size),
                    )
                )

    async def _request(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        if (
            method != "permission"
            or data.get("sessionId") != self.session_id
            or self.stopping
            or not self._running
            or not self._permission
        ):
            return {"decision": "deny"}
        details = bounded_details(
            "\n".join(f"{key}: {value}" for key, value in data.get("details", {}).items())
        )
        selection = await self._permission(
            PermissionRequest(
                str(data.get("toolId", "permission"))[:500],
                tool_title(str(data.get("title", "Tool permission"))),
                (("allow", "Allow once", "allow_once"), ("deny", "Deny", "reject_once")),
                details,
            )
        )
        return {"decision": "allow" if selection == "allow" and not self.stopping else "deny"}

    async def stop(self) -> bool:
        self.stopping = True
        for job in self.rpc.jobs:
            job.cancel()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stop())
        return await asyncio.shield(self._close_task)

    async def _stop(self) -> bool:
        confirmed = False
        try:
            async with asyncio.timeout(35):
                async with self._startup_lock:
                    if self.rpc.owner is None:
                        confirmed = True
                    elif self.session_id and not self.rpc.failed:
                        receipt = await self.rpc.call(
                            "stop", {"sessionId": self.session_id}, timeout=15
                        )
                        proof = CleanupReceipt.model_validate(receipt, strict=True)
                        confirmed = (
                            proof.sessionId == self.session_id
                            and proof.status == "confirmed"
                            and proof.reason is None
                            and proof.checkedNativeOwners == 1
                            and proof.quietObservations >= 2
                            and proof.nativeOwnerExited
                        )
        except (Exception, asyncio.CancelledError):
            pass
        exited = await self.rpc.close()
        self.cleanup_confirmed = confirmed and exited
        return self.cleanup_confirmed


async def model_options(
    directory: Path,
    *,
    cwd: Path | None = None,
    registration: HarnessRegistration | None = None,
    on_cleanup: Callable[[bool], None] | None = None,
) -> list[ModelOption]:
    with tempfile.TemporaryDirectory(prefix="flowfield-catalog-") as temporary:
        if on_cleanup:
            on_cleanup(True)
        agent = ClaudeAgent(
            cwd or Path(temporary).resolve(),
            os.environ,
            registration=registration or HarnessRegistration(harness="claude-code"),
        )
        try:
            if on_cleanup:
                on_cleanup(False)
            async with asyncio.timeout(120):
                await agent.start([])
                models = agent.info.get("models")
                if not isinstance(models, list) or len(models) > 64:
                    raise NativeError("Invalid native catalog")
                result: dict[str, ModelOption] = {}
                for model in models:
                    resolved = model.get("resolvedModel")
                    if not resolved:
                        continue
                    applied = await agent.rpc.call(
                        "selectModel", {"sessionId": agent.session_id, "model": model["id"]}
                    )
                    if (
                        applied.get("model") != resolved
                        or applied.get("sessionId") != agent.session_id
                    ):
                        raise NativeError("Native model identity unconfirmed")
                    result[resolved] = ModelOption(
                        id=resolved,
                        name=model_name(model["name"], resolved),
                        efforts=model["efforts"],
                        modes=[
                            NativeMode(id="default", name="Default"),
                            NativeMode(id="acceptEdits", name="Accept edits"),
                            *(
                                [NativeMode(id="auto", name="Auto (review actions)")]
                                if model.get("autoMode") is True
                                else []
                            ),
                            NativeMode(id="bypassPermissions", name="Bypass permissions"),
                        ],
                        fast=False,
                    )
                return list(result.values())
        finally:
            await agent.close()
            if on_cleanup:
                on_cleanup(agent.cleanup_confirmed)
            if not agent.cleanup_confirmed:
                raise ApplicationError(
                    "agent_cleanup_unconfirmed", "Native catalog cleanup is unconfirmed.", 409
                )
