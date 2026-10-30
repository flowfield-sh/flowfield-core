"""Codex's installed app-server directly; native policy and history remain Codex-owned."""

import asyncio
import base64
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from flowfield import __version__
from flowfield.adapters import codex_usage
from flowfield.adapters.agent_contract import (
    COMMANDS,
    Agent,
    ManagedToolCall,
    McpServer,
    PermissionHandler,
    PermissionRequest,
    bounded_details,
)
from flowfield.adapters.codex_activity import describe
from flowfield.adapters.harness_host import launch_environment, resolve
from flowfield.adapters.json_rpc import MAX_INPUT, JsonRpc, NativeError, NativeRequestUnavailable
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.cost_models import PricingEvidence
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, NativeMode
from flowfield.harness_models import HarnessRegistration
from flowfield.run_activity import ActivityUpdate, ContextUsage
from flowfield.usage_models import Tokens

MODES = [
    NativeMode(id="read-only", name="Read-only"),
    NativeMode(id="workspace-write", name="Workspace access"),
    NativeMode(id="agent", name="Auto review"),
    NativeMode(id="agent-full-access", name="Full access"),
]


def turn_failure(error: Any) -> ApplicationError:
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    if isinstance(info, dict):
        # Retain only structured categories; raw provider messages can contain secrets.
        statuses = [
            value.get("httpStatusCode") for value in info.values() if isinstance(value, dict)
        ]
        info = (
            "unauthorized" if 401 in statuses else "rateLimitExceeded" if 429 in statuses else None
        )
    reasons = {
        "unauthorized": "Codex could not authenticate. Check its sign-in or provider credentials "
        "on the service host, using the paths in Settings → Harnesses, then retry.",
        "usageLimitExceeded": "Codex reached an account usage limit. Check the account's available "
        "usage or choose another available model before retrying.",
        "rateLimitExceeded": "Codex was rate limited. Wait before retrying "
        "or choose another model.",
        "contextWindowExceeded": "Codex ran out of context. Compact the conversation or start "
        "a fresh session before retrying.",
        "serverOverloaded": "Codex's provider is overloaded. Wait before retrying.",
        "badRequest": "Codex's provider rejected the request. Check native provider settings "
        "and the selected model before retrying.",
    }
    fallback = (
        "Codex could not complete this turn. Check its account, provider and model settings "
        "on the service host, then retry."
    )
    return ApplicationError(
        "codex_turn_failed", reasons.get(info, fallback) if isinstance(info, str) else fallback, 409
    )


def command(directory: Path, environment: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    executable = environment.get("CODEX_PATH")
    if not executable:
        raise ApplicationError("harness_missing", "Install Codex on the service host.", 409)
    return [executable, "app-server"], dict(environment)


class CodexAgent(Agent):
    # JSON escaping can expand text sixfold; leave framing to the native adapter.
    max_prompt_bytes = MAX_INPUT // 6

    def __init__(
        self,
        directory: Path,
        cwd: Path,
        environment: Mapping[str, str],
        *,
        registration: HarnessRegistration | None = None,
    ):
        self.launch = resolve(registration or HarnessRegistration(harness="codex"), environment)
        self.environment = launch_environment(self.launch, environment)
        self.environment.pop("APP_SERVER_LOGS", None)
        self.command, self.environment = command(directory, self.environment)
        self.cwd = cwd
        # Native MCP inventories include full tool schemas, and native replies can
        # include retained tool output. Use the bounded large-frame transport.
        self.rpc = JsonRpc(self._event, self._request, frame_limit=MAX_INPUT)
        self.choice: AgentChoice | None = None
        self.models: list[dict[str, Any]] = []
        self.servers: list[McpServer] = []
        self.resume: str | None = None
        self.persistent = False
        self.turns: dict[str, str] = {}
        self.epoch = 0
        self.invalid = False
        self.tools: dict[str, str] = {}
        self._completion: asyncio.Future[str] | None = None
        self._permission: PermissionHandler | None = None
        self._mcp_approvals: dict[str, tuple[str, str, str, ManagedToolCall | None]] = {}
        self._foreground_id: str | None = None
        self._close_task: asyncio.Task[bool] | None = None
        self._startup_lock = asyncio.Lock()
        self._operations: set[asyncio.Task[Any]] = set()
        self.session_id = None
        self.stopping = False
        self.cleanup_confirmed = True
        self._usage_version: str | None = None
        self._price_context: dict[tuple[str, str], PricingEvidence] = {}
        self._rerouted: set[tuple[str, str]] = set()
        self._usage_baselines: dict[str, Tokens] = {}
        self._usage_latest: dict[tuple[str, str], Tokens] = {}
        self._usage_threads: dict[str, bool] = {}
        self._usage_turns: set[tuple[str, str]] = set()
        self._usage_final: dict[tuple[str, str], bool] = {}

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self.rpc.owner.process if self.rpc.owner else None

    async def start(
        self, servers: list[McpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None:
        async with self._startup_lock:
            if self.stopping:
                raise NativeError("Agent stopped before startup")
            self.servers, self.resume, self.persistent = servers, resume, persistent
            self.cleanup_confirmed = False
            assert self.launch and self.launch.native_executable
            await self.rpc.start(self.command, self.cwd, self.environment)
            initialized = await self.rpc.call(
                "initialize",
                {
                    "clientInfo": {"name": "flowfield", "version": __version__},
                    "capabilities": {"experimentalApi": True},
                },
            )
            version = re.match(
                r"[^ /]+/(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)(?:\s|$)",
                str(initialized.get("userAgent", "")),
            )
            self._usage_version = version[1] if version else None
            await self.rpc.send({"method": "initialized", "params": {}})
            self.models = await self._page("model/list", {"includeHidden": False}, 64)

    async def _page(self, method: str, params: dict[str, Any], limit: int) -> list[Any]:
        items: list[Any] = []
        cursors: set[str] = set()
        cursor = None
        for _ in range(16):
            result = await self.rpc.call(
                method,
                {
                    **params,
                    "cursor": cursor,
                    **(
                        {"limit": min(limit, 100)}
                        if method in {"model/list", "thread/loaded/list", "mcpServerStatus/list"}
                        else {}
                    ),
                },
            )
            data, cursor = result.get("data"), result.get("nextCursor")
            if not isinstance(data, list) or not (cursor is None or isinstance(cursor, str)):
                raise NativeError("Invalid native page")
            items.extend(data)
            if len(items) > limit or cursor in cursors:
                raise NativeError("Native catalog or ownership inventory exceeded its bound")
            if cursor is None:
                return items
            cursors.add(cursor)
        raise NativeError("Native pagination exceeded its bound")

    async def configure(self, choice: AgentChoice) -> AgentChoice:
        if self.stopping:
            raise NativeError("Agent is stopping")
        task = asyncio.current_task()
        assert task
        self._operations.add(task)
        try:
            model = next((m for m in self.models if m.get("id") == choice.model), None)
            if not model or choice.mode not in {m.id for m in MODES}:
                raise ValueError("Unavailable model or access mode")
            efforts = [e["reasoningEffort"] for e in model["supportedReasoningEfforts"]]
            if choice.effort not in efforts and (choice.effort is not None or efforts):
                raise ValueError("Unavailable reasoning effort")
            fast = "fast" in model.get("additionalSpeedTiers", []) or any(
                tier.get("id") in {"fast", "priority"} for tier in model.get("serviceTiers", [])
            )
            if choice.fast and not fast:
                raise ValueError("Fast unavailable")
            config = (
                await self.rpc.call("config/read", {"includeLayers": True, "cwd": str(self.cwd)})
            ).get("config", {})
            existing = config.get("mcp_servers", {})
            if not isinstance(existing, dict):
                raise NativeError("Invalid native MCP configuration")
            mcp: dict[str, Any] = {name: {"enabled": False} for name in existing}
            for server in self.servers:
                mcp[server.name] = {
                    "enabled": True,
                    "url": server.url,
                    "http_headers": {h.name: h.value for h in server.headers},
                }
            access = self._access(choice)
            params = {
                "cwd": str(self.cwd),
                "model": model.get("model", choice.model),
                "approvalPolicy": access["approvalPolicy"],
                "approvalsReviewer": access["approvalsReviewer"],
                "sandbox": access["sandbox"],
                "serviceTier": "fast" if choice.fast else "default",
                "config": {"mcp_servers": mcp, "model_reasoning_effort": choice.effort},
            }
            directories = (
                *self.project_directories,
                *((self.output_directory,) if self.output_directory else ()),
            )
            if directories and access["sandbox"] == "workspace-write":
                params["config"]["sandbox_workspace_write.writable_roots"] = [
                    str(path) for path in directories
                ]
            if self.session_id:
                # Coordinator clients are created afresh per turn, so reconfiguration
                # would hide native binding changes and is deliberately rejected.
                raise NativeError("Native choices already applied")
            if self.resume:
                response = await self.rpc.call(
                    "thread/resume", {**params, "threadId": self.resume, "excludeTurns": True}
                )
            else:
                response = await self.rpc.call(
                    "thread/start", {**params, "ephemeral": not self.persistent}
                )
            identity = response.get("thread", {}).get("id")
            if not isinstance(identity, str) or not identity or len(identity) > 500:
                raise NativeError("Invalid native thread identity")
            self.session_id = identity
            self._usage_threads[identity] = bool(self.resume)
            expected_type = {
                "read-only": "readOnly",
                "workspace-write": "workspaceWrite",
                "danger-full-access": "dangerFullAccess",
            }[access["sandbox"]]
            if (
                self.resume
                and identity != self.resume
                or response.get("model") != params["model"]
                or response.get("approvalPolicy") != params["approvalPolicy"]
                or response.get("approvalsReviewer") != params["approvalsReviewer"]
                or response.get("sandbox", {}).get("type") != expected_type
                or response.get("reasoningEffort") != choice.effort
                # Codex accepts the speed alias "fast" and reports its canonical
                # service tier "priority". Both identify the requested Fast mode.
                or (choice.fast and response.get("serviceTier") not in {"fast", "priority"})
                or (not choice.fast and response.get("serviceTier") in {"fast", "priority"})
            ):
                raise NativeError("Native choices were not confirmed")
            if directories and access["sandbox"] == "workspace-write":
                roots = response.get("sandbox", {}).get("writableRoots")
                if not isinstance(roots, list) or not all(
                    isinstance(value, str) for value in roots
                ):
                    raise NativeError("Assigned directory access was not confirmed")
                applied_roots = {Path(value).resolve() for value in roots}
                if not {path.resolve() for path in directories}.issubset(applied_roots):
                    raise NativeError("Assigned directory access was not confirmed")
            if self.stopping:
                raise NativeError("Agent stopped while configuring")
            self.choice = choice.model_copy(deep=True)
            if self.resume and self.on_consumption:
                # Barrier after resume's numeric replay, without fetching turns.
                await self.rpc.call("thread/read", {"threadId": identity, "includeTurns": False})
            return choice.model_copy(deep=True)
        except (ValueError, NativeError) as error:
            raise ApplicationError(
                "agent_resume_failed" if self.resume else "agent_choice_unavailable",
                "Codex could not apply the requested native session settings. "
                "Refresh models or start a fresh session. Nothing was replayed.",
                409,
            ) from error
        finally:
            self._operations.discard(task)

    @staticmethod
    def _access(choice: AgentChoice) -> dict[str, str]:
        return {
            "approvalPolicy": "never" if choice.mode == "agent-full-access" else "on-request",
            "approvalsReviewer": "auto_review" if choice.mode == "agent" else "user",
            "sandbox": "danger-full-access"
            if choice.mode == "agent-full-access"
            else "read-only"
            if choice.mode == "read-only"
            else "workspace-write",
        }

    async def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None,
        *,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        if self.stopping:
            return {"status": "stopped"}
        if not self.choice or not self.session_id or self._completion:
            raise NativeError("Apply native choices before dispatch")
        if text in {"/status", "/mcp", "/skills"}:
            if text == "/status":
                result = " · ".join(
                    [
                        self.choice.model,
                        self.choice.effort or "default effort",
                        self.choice.mode or "default access",
                    ]
                )
            elif text == "/mcp":
                items = await self._page("mcpServerStatus/list", {"threadId": self.session_id}, 64)
                result = "\n".join(
                    str(item.get("name", "MCP"))
                    + " · "
                    + str(item.get("connectionStatus", "unknown"))
                    for item in items
                )
            else:
                response = await self.rpc.call(
                    "skills/list", {"cwds": [str(self.cwd)], "forceReload": False}
                )
                groups = response.get("data", [])
                if not isinstance(groups, list) or len(groups) > 64:
                    raise NativeError("Native skill inventory exceeded its bound")
                skills = [skill for group in groups for skill in group.get("skills", [])]
                if len(skills) > 256:
                    raise NativeError("Native skill inventory exceeded its bound")
                result = "\n".join(str(skill.get("name", "Skill")) for skill in skills)
            self.activity(
                ActivityUpdate(
                    key="command", kind="agent", text=bounded_details(result or "None available.")
                )
            )
            return {"status": "stopped" if self.stopping else "completed"}
        self._foreground_id = None
        self._mcp_approvals.clear()
        self._permission = on_permission
        self._completion = asyncio.get_running_loop().create_future()
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for item in attachments or []:
            content.append({"type": "text", "text": "Human attachment: " + item["name"]})
            if item["mime"].startswith("image/"):
                content.append(
                    {"type": "image", "url": f"data:{item['mime']};base64,{item['data']}"}
                )
            else:
                content.append(
                    {"type": "text", "text": base64.b64decode(item["data"]).decode("utf-8")}
                )
        try:
            if text == "/compact":
                await self.rpc.call("thread/compact/start", {"threadId": self.session_id})
            else:
                await self.rpc.call(
                    "turn/start",
                    {
                        "threadId": self.session_id,
                        "input": content,
                        "model": self.choice.model,
                        "effort": self.choice.effort,
                        "serviceTierForTurn": "fast" if self.choice.fast else "default",
                    },
                )
            return {"status": await self._completion}
        finally:
            self._completion = None
            self._foreground_id = None
            self._permission = None

    def validate_attachments(self, attachments: list[dict[str, str]]) -> None:
        if not any(item["mime"].startswith("image/") for item in attachments):
            return
        model = next(
            (m for m in self.models if self.choice and m.get("id") == self.choice.model), None
        )
        if not model or "image" not in model.get("inputModalities", []):
            raise ApplicationError(
                "attachment_unsupported", "This model cannot receive images.", 409
            )

    async def command_options(self) -> list[AgentCommand]:
        return [item.model_copy() for item in COMMANDS]

    def _event(self, method: str, data: dict[str, Any]) -> None:
        self._usage_event(method, data)
        if method in {"turn/started", "turn/completed", "thread/started", "thread/closed"}:
            self.epoch += 1
            if method.startswith("turn/"):
                identity, turn = data.get("threadId"), data.get("turn", {})
                if (
                    not isinstance(identity, str)
                    or not isinstance(turn.get("id"), str)
                    or len(self.turns) > 64
                ):
                    self.invalid = True
                elif method == "turn/started":
                    self.turns[identity] = turn["id"]
                elif self.turns.get(identity) == turn["id"]:
                    del self.turns[identity]
        if method == "transport/closed":
            if self._completion and not self._completion.done():
                self._completion.set_exception(
                    NativeError("Native process exited; nothing was replayed")
                )
            return
        if data.get("threadId") != self.session_id or not self._completion:
            return
        if method == "turn/started":
            identity = data.get("turn", {}).get("id")
            if self._foreground_id is not None and self._foreground_id != identity:
                self.invalid = True
                if not self._completion.done():
                    self._completion.set_exception(
                        NativeError("Concurrent native foreground turns")
                    )
            else:
                self._foreground_id = identity
        if data.get("turnId") and data["turnId"] != self._foreground_id:
            return
        if method == "turn/completed":
            if data.get("turn", {}).get("id") != self._foreground_id:
                return
            if not self._completion.done():
                status = data.get("turn", {}).get("status")
                if status == "failed":
                    self._completion.set_exception(turn_failure(data.get("turn", {}).get("error")))
                else:
                    self._completion.set_result(
                        "completed" if status == "completed" and not self.stopping else "stopped"
                    )
        elif not self.stopping and method == "item/agentMessage/delta":
            self.activity(
                ActivityUpdate(
                    key="agent-" + str(data.get("itemId", "text"))[:80],
                    kind="agent",
                    text=data.get("delta", ""),
                    append=True,
                )
            )
        elif not self.stopping and method in {"item/started", "item/completed"}:
            item = data.get("item", {})
            kind = item.get("type")
            if kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}:
                identity = str(item.get("id", "tool"))[:90]
                title, details = describe(item)
                if kind == "mcpToolCall":
                    if method == "item/started":
                        self._mcp_approvals[identity] = (
                            str(item.get("server", "")),
                            title,
                            details,
                            ManagedToolCall(
                                str(item.get("server", "")),
                                str(item.get("tool", "")),
                                item["arguments"],
                            )
                            if isinstance(item.get("arguments"), dict)
                            else None,
                        )
                        while len(self._mcp_approvals) > 100:
                            del self._mcp_approvals[next(iter(self._mcp_approvals))]
                    else:
                        self._mcp_approvals.pop(identity, None)
                self.tools[identity] = details
                while len(self.tools) > 100:
                    del self.tools[next(iter(self.tools))]
                status = item.get("status") or (
                    "completed" if method == "item/completed" else "running"
                )
                if status == "inProgress":
                    status = "running"
                self.activity(
                    ActivityUpdate(
                        key=identity,
                        kind="command" if kind == "commandExecution" else "tool",
                        text=title + " · " + str(status) + "\n" + details,
                    )
                )

        elif not self.stopping and method == "thread/tokenUsage/updated":
            usage = data.get("tokenUsage")
            usage = usage if isinstance(usage, dict) else {}
            last = usage.get("last")
            used = last.get("totalTokens") if isinstance(last, dict) else None
            size = usage.get("modelContextWindow")
            if type(used) is int and type(size) is int and size > 0 and used >= 0:
                self.input_tokens_available = max(0, size - used)
                self.activity(
                    ActivityUpdate(
                        key="context",
                        kind="status",
                        text="",
                        context=ContextUsage(used=used, size=size),
                    )
                )

    def _usage_event(self, method: str, data: dict[str, Any]) -> None:
        """Accounting remains bound after Stop; it has no lifecycle/tool authority."""
        if not self.on_consumption:
            return
        thread = data.get("threadId")
        if method == "thread/started":
            native = data.get("thread", {})
            identity, parent = native.get("id"), native.get("parentThreadId")
            source = native.get("source")
            if not parent and isinstance(source, dict):
                child = source.get("subAgent")
                if isinstance(child, dict) and isinstance(child.get("thread_spawn"), dict):
                    parent = child["thread_spawn"].get("parent_thread_id")
            if (
                isinstance(identity, str)
                and len(identity) <= 200
                and isinstance(parent, str)
                and parent in self._usage_threads
                and len(self._usage_threads) < 64
            ):
                self._usage_threads[identity] = (
                    "forkedFromId" not in native or native["forkedFromId"] is not None
                )
            return
        # The root resume replay can precede the thread/resume response.
        if isinstance(thread, str) and thread == self.resume and self.session_id is None:
            if method == "thread/tokenUsage/updated":
                self._usage_baselines[thread] = codex_usage.cumulative(data)
            return
        if not isinstance(thread, str) or thread not in self._usage_threads or not self.session_id:
            return
        turn = data.get("turn", {}).get("id") if method.startswith("turn/") else data.get("turnId")
        if not isinstance(turn, str) or not turn or len(turn) > 120:
            return
        key = (thread, turn)
        if method == "turn/started" and (
            (self._completion is not None and not self.stopping)
            or (thread != self.session_id and bool(self._usage_turns))
        ):
            if len(self._usage_turns) < 4096:
                self._usage_turns.add(key)
        elif method == "model/rerouted" and key in self._usage_turns:
            self._rerouted.add(key)
        elif method == "thread/tokenUsage/updated":
            value = codex_usage.cumulative(data)
            if key not in self._usage_turns:
                if (
                    not any(t == thread for t, _ in self._usage_turns)
                    and self._usage_threads[thread]
                ):
                    self._usage_baselines[thread] = value
                return
            native = data.get("tokenUsage")
            last = codex_usage.counters(native.get("last") if isinstance(native, dict) else None)
            self._price_context[key] = PricingEvidence(
                model=self.choice.model
                if self.choice and thread == self.session_id and key not in self._rerouted
                else None,
                provider="openai",
                input_observed=last.input,
                max_input=last.input,
                service_tier="fast" if self.choice and self.choice.fast else "standard",
            )
            self._usage_latest[key] = value
            self._emit_usage(thread, turn, value)
        elif method == "turn/completed" and key in self._usage_turns:
            self._usage_final[key] = data.get("turn", {}).get("status") != "completed"
            self._emit_usage(thread, turn, self._usage_latest.get(key, Tokens()))

    def _emit_usage(self, thread: str, turn: str, value: Tokens) -> None:
        assert self.on_consumption and self.session_id
        key = (thread, turn)
        self.on_consumption(
            codex_usage.observation(
                value,
                session=self.session_id,
                thread=thread,
                turn=turn,
                version=self._usage_version,
                resumed=self._usage_threads[thread],
                inherited=self._usage_baselines.get(thread),
                final=key in self._usage_final,
                failed=self._usage_final.get(key, False),
                pricing=self._price_context.get(key),
            )
        )

    async def _request(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        def eligible() -> bool:
            return (
                not self.stopping
                and self._completion is not None
                and not self._completion.done()
                and self._permission is not None
                and data.get("threadId") == self.session_id
                and data.get("turnId") == self.turns.get(self.session_id or "")
            )

        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            if not eligible():
                return {"decision": "cancel"}
            assert self._permission
            identity = str(data.get("itemId", "permission"))[:500]
            details = bounded_details(
                "\n".join(
                    [
                        self.tools.get(identity, "") if not data.get("command") else "",
                        *[
                            str(data[key])
                            for key in ("command", "cwd", "reason", "grantRoot")
                            if data.get(key)
                        ],
                    ]
                ).strip()
            )
            from flowfield.adapters.codex_permissions import command_decisions

            options, decisions = command_decisions(data)
            if not options:
                return {"decision": "cancel"}
            selection = await self._permission(
                PermissionRequest(
                    identity,
                    "Command permission" if "commandExecution" in method else "File permission",
                    options,
                    details,
                )
            )
            return {
                "decision": decisions[selection]
                if selection in decisions and eligible()
                else "cancel"
            }
        if method == "item/permissions/requestApproval":
            # Never synthesize a broad permission profile from a boolean decision.
            return {"permissions": {}, "scope": "turn"}
        if method == "item/tool/requestUserInput":
            # Native Codex can route MCP approval through request_user_input
            # instead of elicitation, depending on its feature configuration.
            # Bind this special question to an observed active scoped tool; never
            # turn ordinary model-authored questions into permission grants.
            item = str(data.get("itemId", ""))
            tool = self._mcp_approvals.get(item)
            questions = data.get("questions")
            if (
                eligible()
                and isinstance(questions, list)
                and any(
                    isinstance(question, dict)
                    and isinstance(question.get("id"), str)
                    and not question["id"].startswith("mcp_tool_call_approval_")
                    for question in questions
                )
            ):
                raise NativeRequestUnavailable(
                    "Flowfield did not display these native questions and no human answered. "
                    "Ask planning questions in your conversation reply, or use Flowfield's "
                    "question tool for an Inbox response. Workers must use ask_question."
                )
            if (
                not eligible()
                or tool is None
                or tool[0] not in {server.name for server in self.servers}
                or not isinstance(questions, list)
                or len(questions) != 1
            ):
                return {"answers": {}}
            question = questions[0]
            if (
                not isinstance(question, dict)
                or question.get("id") != "mcp_tool_call_approval_" + item
                or question.get("isOther")
                or question.get("isSecret")
            ):
                return {"answers": {}}
            offered = question.get("options")
            if not isinstance(offered, list) or len(offered) > 16:
                return {"answers": {}}
            labels = {
                option.get("label")
                for option in offered
                if isinstance(option, dict) and isinstance(option.get("label"), str)
            }
            options = tuple(
                option
                for native, option in (
                    ("Allow", ("allow", "Allow once", "allow_once")),
                    ("Cancel", ("deny", "Deny", "reject_once")),
                )
                if native in labels
            )
            if not options:
                return {"answers": {}}
            assert self._permission
            selection = await self._permission(
                PermissionRequest(item, tool[1], options, tool[2], managed_tool=tool[3])
            )
            if (
                not eligible()
                or self._mcp_approvals.get(item) != tool
                or selection not in {option[0] for option in options}
            ):
                return {"answers": {}}
            return {
                "answers": {
                    question["id"]: {"answers": ["Allow" if selection == "allow" else "Cancel"]}
                }
            }
        if method == "mcpServer/elicitation/request":
            # Codex routes MCP tool approvals through an empty form. Only map
            # that known approval shape, bound to this turn and its scoped server;
            # arbitrary forms, authentication and persistent grants stay unsupported.
            cancelled = {"action": "cancel", "content": None, "_meta": None}
            meta, schema = data.get("_meta"), data.get("requestedSchema")
            if (
                not eligible()
                or data.get("serverName") not in {server.name for server in self.servers}
                or data.get("mode") != "form"
                or not isinstance(meta, dict)
                or meta.get("codex_approval_kind") != "mcp_tool_call"
                or not isinstance(schema, dict)
                or schema.get("type") != "object"
                or schema.get("properties") != {}
                or schema.get("required") not in (None, [])
                or set(schema) - {"type", "properties", "required"}
            ):
                return cancelled
            assert self._permission
            message = str(data.get("message", "Tool permission"))
            match = re.search(r'tool ["`]([^"`]+)["`]', message)
            name = str(meta.get("tool_title") or (match[1] if match else "Tool permission"))
            title, details = describe(
                {
                    "type": "mcpToolCall",
                    "server": data["serverName"],
                    "tool": name,
                    "arguments": meta.get("tool_params", {}),
                }
            )
            observed = [
                call[3]
                for call in self._mcp_approvals.values()
                if call[3]
                and call[3].server == data["serverName"]
                and call[3].name == (match[1] if match else None)
                and call[3].arguments == meta.get("tool_params", {})
            ]
            selection = await self._permission(
                PermissionRequest(
                    "mcp-" + name[:490],
                    title,
                    (("allow", "Allow once", "allow_once"), ("deny", "Deny", "reject_once")),
                    bounded_details(message + "\n" + details),
                    managed_tool=observed[0] if len(observed) == 1 else None,
                )
            )
            if not eligible() or selection not in {"allow", "deny"}:
                return cancelled
            return {
                "action": "accept" if selection == "allow" else "decline",
                "content": {} if selection == "allow" else None,
                "_meta": None,
            }
        raise NativeError("Unsupported native request")

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
            async with asyncio.timeout(15):
                async with self._startup_lock:
                    if self.rpc.owner is None:
                        confirmed = True
                    else:
                        pending = [
                            task for task in self._operations if task is not asyncio.current_task()
                        ]
                        if pending:
                            await asyncio.gather(
                                *(asyncio.shield(task) for task in pending), return_exceptions=True
                            )
                        await self._quiesce()
                        confirmed = True
        except (Exception, asyncio.CancelledError):
            pass
        exited = await self.rpc.close()
        self.cleanup_confirmed = confirmed and exited
        return self.cleanup_confirmed

    async def _quiesce(self) -> None:
        checked: set[str] = set()
        previous: tuple[tuple[str, ...], int] | None = None
        stopped = 0
        for _ in range(100):
            if self.invalid or self.rpc.failed:
                raise NativeError("Native ownership unknown")
            epoch = self.epoch
            ids = await self._page("thread/loaded/list", {}, 64)
            if any(not isinstance(i, str) or not i or len(i) > 500 for i in ids) or len(
                set(ids)
            ) != len(ids):
                raise NativeError("Invalid native ownership")
            if (self.session_id and self.session_id not in ids) or not checked.issubset(ids):
                raise NativeError("Native owner disappeared")
            idle = True
            for identity in ids:
                checked.add(identity)
                metadata = (
                    await self.rpc.call(
                        "thread/read", {"threadId": identity, "includeTurns": False}
                    )
                ).get("thread", {})
                if metadata.get("id") != identity or type(metadata.get("ephemeral")) is not bool:
                    raise NativeError("Invalid native owner metadata")
                goal = (
                    None
                    if metadata["ephemeral"]
                    else (await self.rpc.call("thread/goal/get", {"threadId": identity})).get(
                        "goal", {}
                    )
                )
                if goal is not None:
                    status = goal.get("status")
                    if status not in {
                        "active",
                        "paused",
                        "blocked",
                        "usageLimited",
                        "budgetLimited",
                        "complete",
                    }:
                        raise NativeError("Unknown native goal")
                    if status == "active":
                        await self.rpc.call(
                            "thread/goal/set", {"threadId": identity, "status": "paused"}
                        )
                        idle = False
                if identity in self.turns:
                    await self.rpc.call(
                        "turn/interrupt", {"threadId": identity, "turnId": self.turns[identity]}
                    )
                    idle = False
                for terminal in await self._page(
                    "thread/backgroundTerminals/list", {"threadId": identity}, 256
                ):
                    process_id = terminal.get("processId")
                    stopped += 1
                    if not isinstance(process_id, str) or not process_id or stopped > 256:
                        raise NativeError("Invalid native terminal inventory")
                    if (
                        await self.rpc.call(
                            "thread/backgroundTerminals/terminate",
                            {"threadId": identity, "processId": process_id},
                        )
                    ).get("terminated") is not True:
                        raise NativeError("Native terminal stop unconfirmed")
                    idle = False
                state = (
                    await self.rpc.call(
                        "thread/read", {"threadId": identity, "includeTurns": False}
                    )
                ).get("thread", {})
                if state.get("id") != identity or state.get("status", {}).get("type") not in {
                    "idle",
                    "active",
                }:
                    raise NativeError("Native state unconfirmed")
                idle = idle and state["status"]["type"] == "idle"
            signature = (tuple(sorted(ids)), self.epoch)
            if idle and not self.turns and epoch == self.epoch:
                if previous == signature:
                    return
                previous = signature
            else:
                previous = None
            await asyncio.sleep(0.05)
        raise NativeError("Native shutdown exceeded its bound")


def catalog(agent: CodexAgent) -> list[ModelOption]:
    return [
        ModelOption(
            id=m["id"],
            name=m["displayName"],
            efforts=[e["reasoningEffort"] for e in m["supportedReasoningEfforts"]],
            modes=MODES,
            fast="fast" in m.get("additionalSpeedTiers", [])
            or any(t.get("id") in {"fast", "priority"} for t in m.get("serviceTiers", [])),
            fast_description="Faster responses, increased usage.",
        )
        for m in agent.models
        if not m.get("hidden")
    ]


async def model_options(
    directory: Path,
    *,
    registration: HarnessRegistration | None = None,
    cwd: Path | None = None,
    on_cleanup: Callable[[bool], None] | None = None,
) -> list[ModelOption]:
    with tempfile.TemporaryDirectory(prefix="flowfield-catalog-") as temporary:
        if on_cleanup:
            on_cleanup(True)
        agent = CodexAgent(
            directory, cwd or Path(temporary).resolve(), os.environ, registration=registration
        )
        try:
            if on_cleanup:
                on_cleanup(False)
            async with asyncio.timeout(120):
                await agent.start([])
                return catalog(agent)
        finally:
            await agent.close()
            if on_cleanup:
                on_cleanup(agent.cleanup_confirmed)
            if not agent.cleanup_confirmed:
                raise ApplicationError(
                    "agent_cleanup_unconfirmed", "Native catalog cleanup is unconfirmed.", 409
                )
