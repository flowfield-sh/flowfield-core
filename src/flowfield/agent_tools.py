"""Revocable, role-scoped MCP tools over canonical Flowfield operations.

These grants are created by the service, never from agent-supplied role/scope.
Coordinators receive workspace authority and explicit project destinations;
workers have run-bound tools.
"""

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import ValidationError

from flowfield.errors import ApplicationError
from flowfield.mcp import create_mcp
from flowfield.worker_tools import WorkerBridge, worker_tools
from flowfield.workspaces import Workspaces

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
        except ValidationError as error:
            details = "; ".join(
                f"{'.'.join(map(str, item['loc'])) or 'request'}: {item['msg']}"
                for item in error.errors(include_input=False, include_context=False)[:5]
            )
            return failure("invalid_request", details[:2000])

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
                name=item["name"],
                description=item["description"],
                inputSchema=item["inputSchema"],
                annotations=ToolAnnotations(
                    readOnlyHint=item["name"] in {"read_context", "search_context"},
                    destructiveHint=item["name"] == "run_project_checks",
                    openWorldHint=item["name"] == "run_project_checks",
                ),
            )
            for item in worker_tools()
        ],
        call,
    )


async def workspace_scope(
    supervisor: "Supervisor",
    workspace_id: str,
    *,
    author: str = "agent",
    check: Callable[[], None] | None = None,
) -> ScopedTools:
    """Bind workspace authority while keeping every repository destination explicit."""
    Workspaces(supervisor.workspace).get(workspace_id)
    workspace = supervisor.workspace
    canonical = create_mcp(
        lambda: workspace, lambda: supervisor, origin="", include_service_tools=False
    )
    scoped = []
    project_tools = set()
    optional_project_tools = set()
    workspace_tools = set()
    authored_arguments: dict[str, list[str]] = {}
    for tool in await canonical.list_tools():
        schema = copy.deepcopy(tool.inputSchema)
        properties = schema.get("properties", {})
        if "workspace_id" in properties:
            workspace_tools.add(tool.name)
            del properties["workspace_id"]
        if "project_id" in properties:
            project_tools.add(tool.name)
            if "workspace_id" in tool.inputSchema.get(
                "properties", {}
            ) and "project_id" not in schema.get("required", []):
                optional_project_tools.add(tool.name)
        elif tool.name not in workspace_tools and (
            not tool.annotations or not tool.annotations.readOnlyHint
        ):
            raise RuntimeError("Coordinator mutation lacks a workspace or project binding")
        authored_arguments[tool.name] = []
        for key, value in properties.items():
            definition = schema.get("$defs", {}).get(value.get("$ref", "").split("/")[-1], value)
            if "author" in definition.get("properties", {}):
                authored_arguments[tool.name].append(key)
        schema["required"] = [key for key in schema.get("required", []) if key != "workspace_id"]
        schema["additionalProperties"] = False
        scoped.append(tool.model_copy(update={"inputSchema": schema}))

    async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if "workspace_id" in arguments:
            raise ApplicationError("scope_mismatch", "Workspace is fixed by the service.", 403)
        if "project_id" in arguments and name not in project_tools:
            raise ApplicationError("scope_mismatch", "This operation has workspace scope.", 403)
        bound = copy.deepcopy(arguments)
        if name in workspace_tools:
            bound["workspace_id"] = workspace_id
        if name in project_tools and not (
            name in optional_project_tools and bound.get("project_id") is None
        ):
            destination = bound.get("project_id")
            if not isinstance(destination, str) or not destination:
                raise ApplicationError(
                    "project_required", "Choose an explicit project for this operation.", 409
                )
            Workspaces(workspace).require_project(workspace_id, destination)
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
