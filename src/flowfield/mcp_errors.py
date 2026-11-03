"""One application-error contract for public and role-bound MCP calls."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from flowfield.errors import ApplicationError


def failure(code: str, message: str) -> CallToolResult:
    payload = {"error": {"code": code, "message": message}}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=True,
    )


def invalid_request(error: ValidationError) -> CallToolResult:
    details = "; ".join(
        f"{'.'.join(map(str, item['loc'])) or 'request'}: {item['msg']}"
        for item in error.errors(include_input=False, include_context=False)[:5]
    )
    return failure("invalid_request", details[:2000])


class ApplicationMCP(FastMCP):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:  # type: ignore[override]
        # Keep execution on each operation's chosen thread/event loop. FastMCP
        # validates inputs and wraps exceptions before this dispatch boundary.
        try:
            result: Any = await super().call_tool(name, arguments)
        except ToolError as error:
            cause = error.__cause__
            if isinstance(cause, ApplicationError):
                return failure(cause.code, cause.message)
            if isinstance(cause, ValidationError):
                return invalid_request(cause)
            raise
        if isinstance(result, CallToolResult):
            return result
        if isinstance(result, tuple):
            content, structured = result
            return CallToolResult(content=content, structuredContent=structured)
        if isinstance(result, dict):
            if "content" in result:
                return CallToolResult.model_validate(result)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(result))],
                structuredContent=result,
            )
        return CallToolResult(content=list(result))
