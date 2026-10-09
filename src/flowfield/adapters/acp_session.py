"""One ACP session owner for either role. Not a scheduler or an execution sandbox.

No automatic reconnect/replay: after an uncertain operation the caller reconciles
canonical Flowfield state before explicitly creating/loading another session.
"""

import asyncio
import contextlib
import math
from collections.abc import Callable, Coroutine, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, connect_to_agent, text_block
from acp.client import ClientSideConnection
from acp.interfaces import Client
from acp.schema import (
    ClientCapabilities,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallUpdate,
)
from pydantic import BaseModel, ValidationError

from flowfield.adapters.acp_transport import MAX_FRAME, StdioTransport
from flowfield.adapters.local_process import LocalLaunchCancelled, LocalProcess
from flowfield.agent_models import AgentCommand


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class PermissionRequest:
    tool_id: str
    title: str
    options: tuple[tuple[str, str, str], ...]  # id, label, kind
    details: str = ""


@dataclass(frozen=True)
class StopReceipt:
    turn_finished: bool
    process_group_exited: bool
    session_closed: bool | None = None
    process_exited_gracefully: bool = False
    owned_work_stopped: bool | None = None
    # Group exit alone does not establish detached-tool termination. The optional
    # adapter receipt verifies only its declared native cleanup scope, not arbitrary
    # daemons, external services, or containment of the local machine.


PermissionHandler = Callable[[PermissionRequest], Coroutine[Any, Any, str | None]]
CleanupHandler = Callable[[ClientSideConnection, str, dict[str, Any]], Coroutine[Any, Any, bool]]


@dataclass(frozen=True)
class ShutdownTimeouts:
    cancellation: float = 5
    native_cleanup: float = 5
    session: float = 5
    process_exit: float = 5

    def __post_init__(self) -> None:
        if any(
            not math.isfinite(value) or value <= 0
            for value in (self.cancellation, self.native_cleanup, self.session, self.process_exit)
        ):
            raise ValueError("Shutdown deadlines must be positive and finite")


class AcpSession:
    def __init__(
        self,
        on_event: Callable[[AgentEvent], None],
        *,
        on_permission: PermissionHandler | None = None,
        cleanup: CleanupHandler | None = None,
        permission_projection: Callable[[BaseModel], str] | None = None,
        activity_projection: Callable[[BaseModel], str] | None = None,
        request_timeout: float = 30,
        shutdown_timeouts: ShutdownTimeouts | None = None,
    ):
        self.on_event, self.on_permission = on_event, on_permission
        self.activity_projection = activity_projection or activity_locations
        self._tool_activity: dict[str, dict[str, Any]] = {}
        self.cleanup = cleanup
        self.shutdown_timeouts = shutdown_timeouts or ShutdownTimeouts()
        self.permission_projection = permission_projection or permission_details
        self._tool_details: dict[str, str] = {}
        self.request_timeout = request_timeout
        self.state = "new"
        self.session_id: str | None = None
        self.config: list[dict[str, Any]] = []
        self.capabilities: dict[str, Any] = {}
        self.process: asyncio.subprocess.Process | None = None
        self._local_process: LocalProcess | None = None
        self.connection: ClientSideConnection | None = None
        self.transport: StdioTransport | None = None
        self._turn: asyncio.Task[Any] | None = None
        self._permission: asyncio.Task[str | None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._spawn_lock = asyncio.Lock()
        self._close_task: asyncio.Task[StopReceipt] | None = None
        self._event_failed = False
        self.commands: list[AgentCommand] = []
        self.commands_received = asyncio.Event()
        self._early_commands: tuple[str, list[AgentCommand]] | None = None

    async def start(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        mcp_servers: list[HttpMcpServer],
        load_session_id: str | None = None,
        resume_session_id: str | None = None,
        require_resume: bool = False,
        session_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if self.state != "new":
            raise RuntimeError("Session has already been started")
        if load_session_id and resume_session_id:
            raise ValueError("Choose load or resume, not both")
        if not command or not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError(
                "An agent command and existing absolute working directory are required"
            )
        # Adapter-owned ACP options, snapshotted before any asynchronous startup.
        # The Python ACP library places arbitrary keyword arguments inside _meta.
        metadata = deepcopy(dict(session_metadata)) if session_metadata else {}
        self.state = "starting"
        try:
            async with self._spawn_lock:
                try:
                    self._local_process = await LocalProcess.start(
                        command, cwd=cwd, env=env, limit=MAX_FRAME
                    )
                except LocalLaunchCancelled as error:
                    self._local_process = error.owner
                    self.process = error.owner.process
                    raise
                self.process = self._local_process.process
                assert self.process.stdin and self.process.stdout and self.process.stderr
                self._stderr = asyncio.create_task(self._drain_stderr(self.process.stderr))
                self.transport = StdioTransport(self.process.stdout, self.process.stdin)
                self.connection = connect_to_agent(cast(Client, self), self.transport)
            async with asyncio.timeout(self.request_timeout):
                initialized = await self.connection.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(),
                    client_info=Implementation(name="flowfield", version="0.1.0"),
                )
                if initialized.protocol_version != PROTOCOL_VERSION:
                    raise RuntimeError("Agent does not support this ACP protocol version")
                self.capabilities = (
                    initialized.agent_capabilities.model_dump(by_alias=True, exclude_none=True)
                    if initialized.agent_capabilities
                    else {}
                )
                if mcp_servers and not self.capabilities.get("mcpCapabilities", {}).get("http"):
                    raise RuntimeError("Agent does not support scoped HTTP MCP servers")
                if (require_resume or resume_session_id is not None) and "resume" not in (
                    self.capabilities.get("sessionCapabilities", {})
                ):
                    raise RuntimeError("Agent does not support resuming sessions")
                if resume_session_id is not None:
                    self.session_id = resume_session_id
                    resumed = await self.connection.resume_session(
                        cwd=str(cwd),
                        session_id=resume_session_id,
                        mcp_servers=[*mcp_servers],
                        **metadata,
                    )
                    self.config = [
                        item.model_dump(by_alias=True) for item in resumed.config_options or []
                    ]
                elif load_session_id is not None:
                    if not self.capabilities.get("loadSession"):
                        raise RuntimeError("Agent does not support loading sessions")
                    # Bind before load so replay can be filtered; never resend a prompt.
                    self.session_id = load_session_id
                    loaded = await self.connection.load_session(
                        cwd=str(cwd),
                        session_id=load_session_id,
                        mcp_servers=[*mcp_servers],
                        **metadata,
                    )
                    self.config = [
                        item.model_dump(by_alias=True) for item in loaded.config_options or []
                    ]
                else:
                    created = await self.connection.new_session(
                        cwd=str(cwd), mcp_servers=[*mcp_servers], **metadata
                    )
                    self.session_id = created.session_id
                    self.config = [
                        item.model_dump(by_alias=True) for item in created.config_options or []
                    ]
            if self.state != "starting":
                raise RuntimeError("Agent session stopped during startup")
            self.state = "ready"
            if self._early_commands and self._early_commands[0] == self.session_id:
                self.commands = self._early_commands[1]
                self.commands_received.set()
            self._early_commands = None
        except BaseException:
            self.state = "interrupted"
            await self.close()
            raise

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        # Drain without retaining raw agent diagnostics (may contain private content).
        while await stream.read(8192):
            pass

    def _ready(self) -> tuple[ClientSideConnection, str]:
        if self.state != "ready" or self.connection is None or self.session_id is None:
            raise RuntimeError(
                "Agent session is not ready; reconcile interrupted work before retrying"
            )
        return self.connection, self.session_id

    async def select(self, option_id: str, value: str) -> None:
        connection, session_id = self._ready()
        option = next((item for item in self.config if item["id"] == option_id), None)
        choices = [] if option is None else option.get("options", [])
        values = {entry["value"] for group in choices for entry in (group.get("options", [group]))}
        if value not in values:
            raise ValueError("Agent configuration choice is unavailable")
        self.state = "configuring"
        try:
            async with asyncio.timeout(self.request_timeout):
                response = await connection.set_config_option(option_id, session_id, value)
            self.config = [item.model_dump(by_alias=True) for item in response.config_options]
            selected = next((item for item in self.config if item["id"] == option_id), None)
            if selected is None or selected["currentValue"] != value:
                raise RuntimeError("Agent did not apply the requested configuration choice")
            if self.state != "configuring":
                raise RuntimeError("Agent session stopped while configuring")
            self.state = "ready"
        except BaseException:
            self.state = "interrupted"
            await self.close()
            raise

    async def prompt(
        self, text: str, *, content: list[ImageContentBlock | TextContentBlock] | None = None
    ) -> str:
        connection, session_id = self._ready()
        if not text.strip() or len(text.encode()) > MAX_FRAME // 2:
            raise ValueError("Prompt must contain text and fit the session input limit")
        assert self.transport
        self.transport.reset_budget()
        self._tool_details.clear()
        self._tool_activity.clear()
        self.state = "running"
        self._turn = asyncio.create_task(
            connection.prompt(session_id=session_id, prompt=[text_block(text), *(content or [])])
        )
        try:
            response = await asyncio.shield(self._turn)
            if self._event_failed:
                raise RuntimeError("Agent response could not be delivered")
            if self.state == "running":
                self.state = "ready"
            return str(response.stop_reason)
        except BaseException:
            if self.state != "stopping":
                self.state = "interrupted"
                await self.close()
            raise
        finally:
            await self._cancel_permission()
            self._tool_details.clear()
            self._tool_activity.clear()

    async def _cancel_permission(self) -> None:
        if self._permission:
            task = self._permission
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task

    async def session_update(self, session_id: str, update: BaseModel, **kwargs: Any) -> None:
        data = update.model_dump(by_alias=True, exclude_none=True)
        kind = data.pop("sessionUpdate", "")
        if kind == "available_commands_update" and self.state in {
            "starting",
            "ready",
            "configuring",
            "running",
        }:
            if session_id != self.session_id and not (
                self.state == "starting" and self.session_id is None
            ):
                return
            commands = []
            seen = set()
            for item in data.get("availableCommands", [])[:128]:
                try:
                    command = AgentCommand(
                        name=item["name"],
                        description=item["description"][:1000],
                        input_hint=(item.get("input") or {}).get("hint", "")[:200] or None,
                    )
                except (ValidationError, KeyError, TypeError):
                    continue
                if command.name not in seen:
                    commands.append(command)
                    seen.add(command.name)
            if self.session_id is None:
                self._early_commands = (session_id, commands)
            else:
                self.commands = commands
                self.commands_received.set()
            return
        if session_id != self.session_id or self.state not in {
            "starting",
            "ready",
            "configuring",
            "running",
        }:
            return
        # Publish only allowlisted activity facts; never private reasoning or arbitrary payloads.
        if kind == "agent_message_chunk" and self.state == "running":
            content = data.get("content", {})
            if content.get("type") == "text":
                self._emit(AgentEvent("text", {"text": content["text"]}))
        elif kind in {"tool_call", "tool_call_update"} and self.state == "running":
            detail = self.permission_projection(update)
            if detail and (
                data["toolCallId"] not in self._tool_details
                or data.get("content")
                or data.get("rawInput")
            ):
                self._tool_details[data["toolCallId"]] = detail
                while len(self._tool_details) > 32:
                    del self._tool_details[next(iter(self._tool_details))]
            identity = data["toolCallId"]
            public = self._tool_activity.setdefault(identity, {})
            public.update(
                {
                    key: bounded_details(str(data[key]))[:4000]
                    for key in ("toolCallId", "title", "status", "kind")
                    if key in data
                }
            )
            activity_detail = self.activity_projection(update)
            if activity_detail:
                public["details"] = bounded_details(activity_detail)[:4000]
            while len(self._tool_activity) > 100:
                del self._tool_activity[next(iter(self._tool_activity))]
            self._emit(AgentEvent("tool", dict(public)))
        elif kind == "usage_update" and self.state == "running":
            self._emit(
                AgentEvent("usage", {key: data[key] for key in ("used", "size") if key in data})
            )
        elif kind == "config_option_update":
            self.config = data["configOptions"]

    def _emit(self, event: AgentEvent) -> None:
        try:
            self.on_event(event)
        except Exception:
            # The SDK logs callback errors and otherwise continues the prompt. A
            # lost application event must instead interrupt work, never look saved.
            self._event_failed = True
            if self._turn:
                self._turn.cancel()

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        cancelled = RequestPermissionResponse.model_validate({"outcome": {"outcome": "cancelled"}})
        if (
            session_id != self.session_id
            or self.state != "running"
            or self.on_permission is None
            or self._permission is not None
        ):
            return cancelled
        request = PermissionRequest(
            tool_call.tool_call_id,
            bounded_details(
                tool_call.title
                or self._tool_activity.get(tool_call.tool_call_id, {}).get("title")
                or "Tool permission"
            )[:4000],
            tuple((item.option_id, item.name, item.kind) for item in options),
            bounded_details(
                "\n\n".join(
                    dict.fromkeys(
                        filter(
                            None,
                            [
                                self._tool_details.get(tool_call.tool_call_id, ""),
                                self.permission_projection(tool_call),
                            ],
                        )
                    )
                )
            ),
        )
        task = self._permission = asyncio.create_task(self.on_permission(request))
        try:
            selected = await task
            if self.state != "running" or selected not in {item.option_id for item in options}:
                return cancelled
            return RequestPermissionResponse.model_validate(
                {
                    "outcome": {"outcome": "selected", "optionId": selected},
                }
            )
        except asyncio.CancelledError:
            return cancelled
        finally:
            self._permission = None

    async def close(self, *, timeouts: ShutdownTimeouts | None = None) -> StopReceipt:
        if self._close_task is None:
            self.state = "stopping"
            self._close_task = asyncio.create_task(
                self._shutdown(timeouts or self.shutdown_timeouts)
            )
        # Cancelling an HTTP caller must not abandon an owned agent process.
        return await asyncio.shield(self._close_task)

    async def _shutdown(self, timeouts: ShutdownTimeouts) -> StopReceipt:
        self.state = "stopping"
        async with self._spawn_lock:
            pass  # A concurrently starting process must acquire its owner first.
        turn_finished = self._turn is None
        if self._turn is not None and self.connection is not None:
            try:
                async with asyncio.timeout(timeouts.cancellation):
                    if not self._turn.done() and self.session_id:
                        await self.connection.cancel(session_id=self.session_id)
                    await self._cancel_permission()
                    await asyncio.shield(self._turn)
                turn_finished = True
            except (Exception, asyncio.CancelledError):
                pass
        await self._cancel_permission()
        owned_work_stopped = None
        if self.cleanup is not None:
            owned_work_stopped = False
            if self.connection is not None and self.session_id is not None:
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    async with asyncio.timeout(timeouts.native_cleanup):
                        owned_work_stopped = await self.cleanup(
                            self.connection, self.session_id, self.capabilities
                        )
        session_closed = None
        if (
            self.connection
            and self.session_id
            and "close" in self.capabilities.get("sessionCapabilities", {})
        ):
            session_closed = False
            try:
                async with asyncio.timeout(timeouts.session):
                    await self.connection.close_session(self.session_id)
                session_closed = True
            except (Exception, asyncio.CancelledError):
                pass
        if self.connection:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(timeouts.session):
                    await self.connection.close()
        # EOF gives the bridge its native shutdown path before escalating to
        # process-group signals. In particular, Codex bridges forward EOF to the
        # app-server. A session-close acknowledgment is not a tool-stop receipt.
        graceful_exit = False
        if self.process is not None:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(timeouts.process_exit):
                    graceful_exit = await self.process.wait() == 0
        exited = True
        if self._local_process is not None:
            exited = await self._local_process.close(timeout=timeouts.process_exit)
        if self._turn:
            self._turn.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._turn
        if self._stderr:
            self._stderr.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr
        self.state = (
            "closed"
            if exited
            and turn_finished
            and session_closed is not False
            and owned_work_stopped is not False
            else "interrupted"
        )
        return StopReceipt(turn_finished, exited, session_closed, graceful_exit, owned_work_stopped)


def activity_locations(tool: BaseModel) -> str:
    data = tool.model_dump(by_alias=True, exclude_none=True)
    return "\n".join(
        f"Path: {location['path']}"
        for location in data.get("locations", [])
        if isinstance(location.get("path"), str)
    )


def bounded_details(value: str) -> str:
    from flowfield.run_activity import clean

    value = clean(value)
    return value if len(value) <= 16000 else value[:15970] + "\n[Details truncated]"


def permission_details(tool: BaseModel) -> str:
    """Public ACP text/diff content only; never rawInput, metadata or resource bodies."""
    data = tool.model_dump(by_alias=True, exclude_none=True)
    parts = [
        f"Path: {location['path']}"
        for location in data.get("locations", [])
        if isinstance(location.get("path"), str)
    ]
    for item in data.get("content", []):
        if item.get("type") == "content" and item.get("content", {}).get("type") == "text":
            parts.append(item["content"]["text"])
        elif item.get("type") == "diff":
            parts.append(
                f"File: {item.get('path', '')}\nBefore:\n{item.get('oldText', '')}\n"
                f"After:\n{item.get('newText', '')}"
            )
    return bounded_details("\n\n".join(parts))
