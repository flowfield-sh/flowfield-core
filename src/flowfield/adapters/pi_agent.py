"""Installed Pi RPC, with scoped native MCP and no separate support installation."""

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from uuid import uuid4

from flowfield.activity_text import command_title, mcp_title
from flowfield.adapters.agent_contract import Agent, McpServer, PermissionHandler, bounded_details
from flowfield.adapters.harness_host import launch_environment, resolve
from flowfield.adapters.json_rpc import MAX_INPUT, NativeError
from flowfield.adapters.pi_rpc import PiRpc
from flowfield.agent_models import AgentChoice
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, NativeMode
from flowfield.harness_models import HarnessRegistration
from flowfield.run_activity import ActivityUpdate, ContextUsage

MODE = NativeMode(
    id="full-access", name="Full access", description="Pi tools run without approval prompts."
)


def model_id(model: dict[str, Any]) -> str:
    return (
        quote(str(model.get("provider", "")), safe="")
        + "/"
        + quote(str(model.get("id", "")), safe="")
    )


class PiAgent(Agent):
    max_prompt_bytes = MAX_INPUT // 6

    def __init__(
        self,
        directory: Path,
        cwd: Path,
        environment: Mapping[str, str],
        *,
        registration: HarnessRegistration,
    ):
        self.launch = resolve(registration, environment)
        self.environment = launch_environment(self.launch, environment)
        self.directory, self.cwd = directory, cwd
        # Pi includes authoritative whole messages alongside streaming deltas.
        self.rpc = PiRpc(self._event, self._request, frame_limit=MAX_INPUT)
        self.choice: AgentChoice | None = None
        self.model: dict[str, Any] = {}
        self._completion: asyncio.Future[str] | None = None
        self._ready = asyncio.Event()
        self._shutdown = False
        self._startup_lock = asyncio.Lock()
        self._close_task: asyncio.Task[bool] | None = None
        self._message = 0
        self._tools: dict[str, dict[str, Any]] = {}
        self._error = False
        self.session_id = None
        self.stopping = False
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
            assert self.launch and self.launch.native_executable
            if resume and not re.fullmatch(r"[a-zA-Z0-9_.-]{1,200}", resume):
                raise NativeError("Invalid Pi session identity")
            # Pi's provider tool names have a 64-character ceiling. Keep scoped
            # server identities stable and short so real tool names stay intact.
            servers = [
                server.model_copy(
                    update={
                        "name": "flowfield_" + hashlib.sha256(server.name.encode()).hexdigest()[:16]
                    }
                )
                if server.name.startswith("flowfield_") and len(server.name) > 26
                else server
                for server in servers
            ]
            sessions = self.directory / "pi-sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            command = [
                self.launch.native_executable,
                "--mode",
                "rpc",
                "--no-extensions",
                "--no-approve",
                "--session-dir",
                str(sessions),
                "--extension",
                str(Path(__file__).with_name("pi_extension.js")),
            ]
            if servers:
                command += ["--extension", "builtin:mcp"]
                tools = ["read", "bash", "edit", "write", "grep", "find", "ls"]
                tools += ["mcp__" + server.name + "__*" for server in servers]
                command += ["--tools", ",".join(tools)]
            else:
                command += ["--no-mcp"]
            if resume:
                command += ["--session", resume]
            elif persistent:
                command += ["--session-id", uuid4().hex]
            else:
                command += ["--no-session"]
            env = {
                **self.environment,
                "FLOWFIELD_PI_SERVERS": json.dumps(
                    [{"name": server.name, "config": server.native()} for server in servers]
                ),
            }
            self.cleanup_confirmed = False
            await self.rpc.start(command, self.cwd, env)
            try:
                state = await self.rpc.call("get_state", {})
                async with asyncio.timeout(15):
                    await self._ready.wait()
                identity = state.get("sessionId")
                if (
                    not isinstance(identity, str)
                    or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,200}", identity)
                    or (resume and identity != resume)
                ):
                    raise NativeError("Pi session identity unconfirmed")
                self.session_id = identity
            except (NativeError, TimeoutError) as error:
                raise ApplicationError(
                    "agent_resume_failed" if resume else "pi_start_failed",
                    "Pi could not open the requested session. Use Pi 1.1 or newer "
                    "and check native settings. Nothing was replayed.",
                    409,
                ) from error

    async def configure(self, choice: AgentChoice) -> AgentChoice:
        if self.stopping or not self.session_id or self._completion is not None:
            raise NativeError("Pi is unavailable for configuration")
        self.choice = None
        provider, separator, name = choice.model.partition("/")
        if (
            choice.harness != "pi"
            or not separator
            or not provider
            or not name
            or choice.mode != MODE.id
            or choice.fast
        ):
            raise ApplicationError(
                "agent_choice_unavailable", "Choose an available Pi model and access mode.", 409
            )
        self.model = await self.rpc.call(
            "set_model", {"provider": unquote(provider), "modelId": unquote(name)}
        )
        levels = (await self.rpc.call("get_available_thinking_levels", {})).get("levels", [])
        if model_id(self.model) != choice.model or choice.effort not in levels:
            raise ApplicationError(
                "agent_choice_unavailable",
                "Pi did not confirm the requested model or effort. Refresh models.",
                409,
            )
        await self.rpc.call("set_thinking_level", {"level": choice.effort})
        state = await self.rpc.call("get_state", {})
        if (
            state.get("sessionId") != self.session_id
            or model_id(state.get("model", {})) != choice.model
            or state.get("thinkingLevel") != choice.effort
        ):
            raise NativeError("Pi did not apply the requested settings")
        self.choice = choice.model_copy(deep=True)
        await self._context()
        return self.choice.model_copy(deep=True)

    async def _context(self) -> None:
        stats = await self.rpc.call("get_session_stats", {})
        context = stats.get("contextUsage", {})
        used, size = context.get("tokens"), context.get("contextWindow")
        if type(used) is int and used >= 0 and type(size) is int and size > 0:
            reserve = self.model.get("maxTokens")
            self.input_tokens_available = (
                max(0, size - used - reserve) if type(reserve) is int and reserve > 0 else None
            )
            self.activity(
                ActivityUpdate(
                    key="context",
                    kind="status",
                    text="",
                    context=ContextUsage(used=used, size=size),
                )
            )
        else:
            self.input_tokens_available = None

    def validate_attachments(self, attachments: list[dict[str, str]]) -> None:
        if any(
            item["mime"].startswith("image/") for item in attachments
        ) and "image" not in self.model.get("input", []):
            raise ApplicationError(
                "agent_image_unsupported", "This Pi model does not accept images.", 409
            )

    async def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None,
        *,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        if self.stopping:
            return {"status": "stopped"}
        if not self.choice or self._completion is not None:
            raise NativeError("Apply Pi settings before dispatch")
        self.validate_attachments(attachments or [])
        images = []
        for item in attachments or []:
            text += "\nHuman attachment: " + item["name"] + "\n"
            if item["mime"].startswith("image/"):
                images.append({"type": "image", "mimeType": item["mime"], "data": item["data"]})
            else:
                text += base64.b64decode(item["data"]).decode("utf-8")
        self._error = False
        self._tools.clear()
        completion = self._completion = asyncio.get_running_loop().create_future()
        try:
            response = await self.rpc.call("prompt", {"message": text, "images": images})
            if response.get("disposition") != "started":
                raise NativeError("Pi did not start the requested turn; nothing was replayed")
            async with asyncio.timeout(3600):
                result = await completion
            if result == "failed":
                raise NativeError("Pi could not complete the turn. Check its provider settings.")
            if not self.stopping:
                await self._context()
            return {"status": result}
        finally:
            self._completion = None
            if not completion.done():
                completion.cancel()
            elif not completion.cancelled():
                completion.exception()  # Observe an early disconnect before prompt acknowledgement.

    def _event(self, method: str, data: dict[str, Any]) -> None:
        if method == "extension_ui_request" and data.get("method") == "notify":
            if data.get("message") == "flowfield:ready:v1":
                self._ready.set()
            elif data.get("message") == "flowfield:closed:v1":
                self._shutdown = True
            return
        completion = self._completion
        if method == "transport/closed":
            if completion is not None and not completion.done():
                completion.set_exception(NativeError("Pi connection closed; nothing was replayed"))
            return
        if completion is None or completion.done():
            return
        if method == "agent_settled":
            completion.set_result(
                "stopped"
                if self.stopping or data.get("aborted")
                else "failed"
                if self._error
                else "completed"
            )
            return
        if self.stopping:
            return
        if method == "message_start" and data.get("message", {}).get("role") == "assistant":
            self._message += 1
        elif method == "message_update":
            event = data.get("assistantMessageEvent", {})
            if event.get("type") == "text_delta" and isinstance(event.get("delta"), str):
                self.activity(
                    ActivityUpdate(
                        key=f"message-{self._message}",
                        kind="agent",
                        text=event["delta"],
                        append=True,
                    )
                )
        elif method == "message_end":
            message = data.get("message", {})
            if message.get("role") == "assistant":
                self._error = message.get("stopReason") in {"error", "aborted"}
                text = "\n".join(
                    item["text"]
                    for item in message.get("content", [])
                    if item.get("type") == "text" and isinstance(item.get("text"), str)
                )
                if text:
                    self.activity(
                        ActivityUpdate(key=f"message-{self._message}", kind="agent", text=text)
                    )
        elif method in {"tool_execution_start", "tool_execution_end"}:
            identity = str(data.get("toolCallId", "tool"))
            if method == "tool_execution_start":
                self._tools[identity] = data
                while len(self._tools) > 100:
                    del self._tools[next(iter(self._tools))]
            tool = self._tools.get(identity, data)
            name, args = str(tool.get("toolName", "Tool")), tool.get("args", {})
            if name.startswith("mcp__"):
                parts = name.split("__", 2)
                title = mcp_title(parts[1], parts[-1])
                details = "Arguments: " + json.dumps(args, ensure_ascii=False)
            else:
                title = (
                    command_title(str(args.get("command", "Run command")))
                    if name == "bash"
                    else name.capitalize()
                )
                details = json.dumps(args, ensure_ascii=False)
            status = (
                "running"
                if method == "tool_execution_start"
                else "failed"
                if data.get("isError")
                else "completed"
            )
            if data.get("isError"):
                details += "\n" + "\n".join(
                    item.get("text", "")
                    for item in data.get("result", {}).get("content", [])
                    if item.get("type") == "text"
                )
            self.activity(
                ActivityUpdate(
                    key=identity[:100],
                    kind="command" if name == "bash" else "tool",
                    text=f"{title} · {status}\n{bounded_details(details)}",
                )
            )

    async def _request(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        # Managed Pi loads no third-party extensions. Never translate an arbitrary
        # extension dialog into a tool grant or an answer on the human's behalf.
        return {"cancelled": True}

    async def stop(self) -> bool:
        self.stopping = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stop())
        return await asyncio.shield(self._close_task)

    async def _stop(self) -> bool:
        confirmed = False
        try:
            async with asyncio.timeout(30):
                async with self._startup_lock:
                    if self.rpc.owner is None:
                        confirmed = True
                    elif self._ready.is_set() and not self.rpc.failed:
                        # Native abort alone continues queued work; seal that first.
                        await self.rpc.call("clear_queue", {})
                        await self.rpc.call("abort", {})
                        await self.rpc.call("abort_bash", {})
                        confirmed = True
                        for _ in range(2):
                            state = await self.rpc.call("get_state", {})
                            if (
                                state.get("isStreaming") is not False
                                or state.get("isCompacting") is not False
                                or state.get("pendingMessageCount") != 0
                                or (self.session_id and state.get("sessionId") != self.session_id)
                            ):
                                confirmed = False
                            await asyncio.sleep(0.05)
        except (Exception, asyncio.CancelledError):
            confirmed = False
        exited = await self.rpc.close()
        self.cleanup_confirmed = confirmed and exited and (self.rpc.owner is None or self._shutdown)
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
        agent = PiAgent(
            directory,
            cwd or Path(temporary).resolve(),
            os.environ,
            registration=registration or HarnessRegistration(harness="pi"),
        )
        try:
            if on_cleanup:
                on_cleanup(False)
            async with asyncio.timeout(120):
                await agent.start([])
                models = (await agent.rpc.call("get_available_models", {})).get("models")
                if not isinstance(models, list) or len(models) > 2048:
                    raise NativeError("Invalid Pi model catalog")
                result = []
                for model in models:
                    identity = model_id(model)
                    if not model.get("provider") or not model.get("id") or len(identity) > 200:
                        continue
                    selected = await agent.rpc.call(
                        "set_model", {"provider": model["provider"], "modelId": model["id"]}
                    )
                    if model_id(selected) != identity:
                        raise NativeError("Pi model identity unconfirmed")
                    levels = (await agent.rpc.call("get_available_thinking_levels", {})).get(
                        "levels"
                    )
                    if (
                        not isinstance(levels, list)
                        or not levels
                        or any(not isinstance(level, str) or len(level) > 40 for level in levels)
                    ):
                        raise NativeError("Invalid Pi thinking levels")
                    result.append(
                        ModelOption(
                            id=identity,
                            name=f"{model.get('name') or model['id']} · {model['provider']}",
                            efforts=levels,
                            modes=[MODE],
                        )
                    )
                return result
        finally:
            await agent.close()
            if on_cleanup:
                on_cleanup(agent.cleanup_confirmed)
            if not agent.cleanup_confirmed:
                raise ApplicationError(
                    "agent_cleanup_unconfirmed", "Pi catalog cleanup is unconfirmed.", 409
                )
