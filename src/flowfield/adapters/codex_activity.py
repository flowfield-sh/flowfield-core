"""Public Codex tool labels and bounded diagnostic details."""

import json
from typing import Any

from flowfield.activity_text import command_title, mcp_title, public_output
from flowfield.adapters.agent_contract import bounded_details


def describe(item: dict[str, Any]) -> tuple[str, str]:
    kind = item["type"]
    details = "\n".join(str(item[key]) for key in ("command", "cwd", "url") if key in item)
    title = {"fileChange": "Edit files", "webSearch": "Search web"}.get(kind, kind)
    if kind == "commandExecution":
        title = command_title(str(item.get("command", "Run command")))
        if item.get("exitCode") is not None:
            details += f"\nExit code: {item['exitCode']}"
        if item.get("aggregatedOutput"):
            details += "\nOutput:\n" + str(item["aggregatedOutput"])
    elif kind == "fileChange":
        details = "\n".join(
            str(change.get("path", "")) + "\n" + str(change.get("diff", ""))
            for change in item.get("changes", [])
        )
    elif kind == "mcpToolCall":
        server = str(item.get("server", "MCP"))
        title = mcp_title(server, str(item.get("tool", "Tool")))
        arguments = item.get("arguments", {})
        if isinstance(arguments, dict):
            target = next(
                (arguments[key] for key in ("task_id", "path", "url") if key in arguments), None
            )
            if isinstance(target, str):
                title += " · " + target
        details = "Arguments: " + json.dumps(arguments, ensure_ascii=False)
        result = item.get("result") or {}
        structured = result.get("structuredContent")
        error = item.get("error") or (
            structured.get("error") if isinstance(structured, dict) else None
        )
        message = error.get("message") if isinstance(error, dict) else error
        if not message and result.get("isError"):
            message = "\n".join(
                block.get("text", "")
                for block in result.get("content", [])
                if block.get("type") == "text"
            )
        if message:
            details = "Error: " + str(message) + "\n" + details
        output = public_output(result) if result else ""
        if output:
            details += "\nOutput:\n" + output
    elif kind == "webSearch":
        details = public_output(item.get("action") or item.get("query"))
    return bounded_details(title).replace("\n", " ")[:200], bounded_details(details)
