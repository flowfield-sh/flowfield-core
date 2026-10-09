"""Revocable, role-scoped MCP tools over canonical Flowfield operations.

These grants are created by the service, never from agent-supplied role/scope.
Coordinators receive the complete project interface; workers have explicit run-bound tools.
"""

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import ValidationError

from flowfield.errors import ApplicationError
from flowfield.mcp import create_mcp
from flowfield.worker_tools import WorkerBridge, worker_tools

if TYPE_CHECKING:
    from flowfield.supervisor import Supervisor


class ScopedTools:
    def __init__(
        self,
        tools: list[Tool],
        call: Callable[[str, dict[str, Any]], Awaitable[CallToolResult]],
        *,
        parallel_reads: bool = False,
        check: Callable[[], None] | None = None,
    ):
        self.tools = {tool.name: tool for tool in tools}
        self._call = call
        self.revoked = False
        self._lock = asyncio.Lock()
        self._parallel_reads = parallel_reads
        self._reading = 0
        self._check = check
        self.server: Server[Any] = Server("flowfield-scoped")

        @self.server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_tools() -> list[Tool]:
            return [] if self.revoked else list(self.tools.values())

        @self.server.call_tool()  # type: ignore[untyped-decorator]
        async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
            return await self.call(name, arguments)

    async def call(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        try:
            if self.revoked:
                raise ApplicationError("scope_closed", "This agent's tool access has ended.", 403)
            if self._check:
                self._check()
            if name not in self.tools:
                raise ApplicationError(
                    "operation_denied", "Operation is outside this role's scope.", 403
                )
            # Coordinator reads use canonical snapshots and may overlap. Native
            # agents routinely batch independent reads; do not turn that into a
            # spurious tool failure. Bound in-flight work without a waiting queue.
            annotation = self.tools[name].annotations
            if self._parallel_reads and annotation and annotation.readOnlyHint:
                if self._reading >= 8:
                    raise ApplicationError(
                        "tool_busy", "Too many concurrent reads. Retry after one finishes.", 409
                    )
                self._reading += 1
                try:
                    return await self._call(name, arguments)
                finally:
                    self._reading -= 1
            # No unbounded waiting tool queue; a result cannot overtake an active command.
            if self._lock.locked():
                raise ApplicationError(
                    "tool_busy", "Wait for the current operation to finish.", 409
                )
            async with self._lock:
                return await self._call(name, arguments)
        except ApplicationError as error:
            return failure(error.code, error.message)
        except ValidationError:
            return failure("invalid_request", "Tool input does not match its schema.")

    def revoke(self) -> None:
        # Already-running operations remain owned by their executor; revoking access
        # does not claim to cancel a command or roll back an accepted mutation.
        self.revoked = True


def failure(code: str, message: str) -> CallToolResult:
    payload = {"error": {"code": code, "message": message}}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=True,
    )


def worker_scope(bridge: WorkerBridge) -> ScopedTools:
    async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
        result = await bridge.call(name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=result)])

    return ScopedTools(
        [
            Tool(
                name=item["name"], description=item["description"], inputSchema=item["inputSchema"]
            )
            for item in worker_tools()
        ],
        call,
    )


async def coordinator_scope(
    supervisor: "Supervisor",
    project_id: str,
    *,
    author: str = "agent",
    check: Callable[[], None] | None = None,
) -> ScopedTools:
    workspace = supervisor.workspace
    workspace.project(project_id)
    canonical = create_mcp(
        lambda: workspace, lambda: supervisor, origin="", include_workspace_tools=False
    )
    scoped = []
    project_tools = set()
    authored_arguments: dict[str, list[str]] = {}
    for tool in await canonical.list_tools():
        schema = copy.deepcopy(tool.inputSchema)
        properties = schema.get("properties", {})
        if "project_id" in properties:
            project_tools.add(tool.name)
            del properties["project_id"]
        elif not tool.annotations or not tool.annotations.readOnlyHint:
            raise RuntimeError("Project mutation lacks a project binding")
        authored_arguments[tool.name] = []
        for key, value in properties.items():
            definition = schema.get("$defs", {}).get(value.get("$ref", "").split("/")[-1], value)
            if "author" in definition.get("properties", {}):
                authored_arguments[tool.name].append(key)
        schema["required"] = [key for key in schema.get("required", []) if key != "project_id"]
        schema["additionalProperties"] = False
        scoped.append(tool.model_copy(update={"inputSchema": schema}))

    async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if "project_id" in arguments:
            raise ApplicationError("scope_mismatch", "Project is fixed by the service.", 403)
        bound = copy.deepcopy(arguments)
        if name in project_tools:
            bound["project_id"] = project_id
        # Attribute every authored request, including omitted model defaults, to
        # its service-owned coordinator turn rather than impersonating the human.
        for key in authored_arguments[name]:
            if isinstance(bound.get(key), dict):
                bound[key]["author"] = author
        # FastMCP's annotation omits its CallToolResult and structured tuple variants.
        try:
            result: Any = await canonical.call_tool(name, bound)
        except ToolError as error:
            # FastMCP wraps exceptions from execution tools. Preserve ordinary
            # application/validation failures as tool results, not failed turns.
            if isinstance(error.__cause__, (ApplicationError, ValidationError)):
                raise error.__cause__ from error
            raise
        if isinstance(result, CallToolResult):
            return result
        if isinstance(result, tuple):
            content, structured = result
            return CallToolResult(content=content, structuredContent=structured)
        if isinstance(result, dict):
            # FastMCP preserves CallToolResult as its serialized protocol object.
            if "content" in result:
                return CallToolResult.model_validate(result)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(result))],
                structuredContent=result,
            )
        return CallToolResult(content=list(result))

    return ScopedTools(scoped, call, parallel_reads=True, check=check)
