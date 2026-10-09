"""Public Codex tool labels and bounded diagnostic details."""

import json
import re
from typing import Any

from flowfield.activity_text import command_title
from flowfield.adapters.agent_contract import bounded_details


def describe(item: dict[str, Any]) -> tuple[str, str]:
    kind = item["type"]
    details = "\n".join(str(item[key]) for key in ("command", "cwd", "url") if key in item)
    title = {"fileChange": "Edit files", "webSearch": "Search web"}.get(kind, kind)
    if kind == "commandExecution":
        title = command_title(str(item.get("command", "Run command")))
        if item.get("exitCode") is not None:
            details += f"\nExit code: {item['exitCode']}"
    elif kind == "fileChange":
        details = "\n".join(
            str(change.get("path", "")) + "\n" + str(change.get("diff", ""))
            for change in item.get("changes", [])
        )
    elif kind == "mcpToolCall":
        server = str(item.get("server", "MCP"))
        if re.fullmatch(r"flowfield(?:_[a-f0-9]+)?", server):
            server = "Flowfield"
        title = f"{server} · {str(item.get('tool', 'Tool')).replace('_', ' ').capitalize()}"
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
    return bounded_details(title).replace("\n", " ")[:200], bounded_details(details)
