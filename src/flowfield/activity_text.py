"""Independent storage and display bounds for public worker output."""

import json
import re
import shlex
from typing import Any

from pydantic import BaseModel


def public_output(value: Any) -> str:
    """Readable tool content, without embedding binary payloads or duplicate JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list) and content:
            return public_output(content)
        structured = value.get("structuredContent")
        if structured is None and ("content" in value or "structuredContent" in value):
            return ""
        return json.dumps(structured if structured is not None else value, ensure_ascii=False)
    if isinstance(value, list):
        return "\n".join(
            str(block.get("text", ""))
            if block.get("type") == "text"
            else f"[{block.get('type', 'Non-text')} content]"
            for block in value
            if isinstance(block, dict)
        )
    return "" if value is None else str(value)


class ActionPresentation(BaseModel):
    title: str
    details: str
    output: str
    output_preview: str


def action_presentation(text: str, kind: str) -> ActionPresentation | None:
    if kind not in {"tool", "command"}:
        return None
    title, _, body = text.partition("\n")
    command = title.rsplit(" · ", 1)[0]
    details, separator, output = body.partition("\nOutput:\n")
    if body.startswith("Output:\n"):
        details, separator, output = "", "Output:\n", body.removeprefix("Output:\n")
    if kind == "command":
        exit_code = re.search(r"(?m)^Exit code: (-?\d+)$", details)
        if exit_code and int(exit_code[1]) != 0:
            title = command + f" · failed (exit {exit_code[1]})"
    if not separator and details.startswith("Error: "):
        output, _, details = details.partition("\n")
        separator = "Error"
    # New records already have a human-readable header. Avoid repeating a one-line
    # command as source; retain multiline scripts and historical transport labels.
    first, newline, rest = details.partition("\n")
    if kind == "command" and first.removeprefix("command: ") == command:
        details = rest if newline else ""
    leading = "\n".join(output.splitlines()[:4])[:500]
    return ActionPresentation(
        title=title,
        details=details.strip(),
        output=output if separator else "",
        output_preview=leading + ("…" if len(leading) < len(output) else ""),
    )


def mcp_title(server: str, tool: str) -> str:
    if re.fullmatch(r"flowfield(?:_[a-f0-9]+)?", server):
        server = "Flowfield"
    return f"{server} · {tool.replace('_', ' ').capitalize()}"


def command_title(command: str) -> str:
    try:
        parts = shlex.split(command)
        if (
            len(parts) == 3
            and parts[0].rsplit("/", 1)[-1] in {"sh", "bash", "zsh", "dash", "fish"}
            and parts[1] in {"-c", "-lc"}
        ):
            command = parts[2]
    except ValueError:
        pass
    line = command.splitlines()[0] if command else "Run command"
    return line[:157] + "…" if len(line) > 160 else line


OMISSION = "\n… middle omitted …\n"


def retain(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = (limit - len(OMISSION)) // 2
    return text[:head] + OMISSION + text[-(limit - head - len(OMISSION)) :]


def preview(text: str, kind: str) -> str:
    if kind == "command" and ("\n" in text or len(text) > 200):
        lines = text.splitlines()
        # Older records put a transport label before the command. Keep them readable
        # without rewriting saved evidence or hiding the useful command behind it.
        if lines[0].startswith(("commandExecution ·", "Bash ·")) and len(lines) > 1:
            return (
                command_title(lines[1].removeprefix("command: "))
                + " · "
                + lines[0].split(" · ", 1)[1]
            )
        end = lines[-1] if lines[-1].startswith(("Exit code:", "Command interrupted")) else ""
        return command_title(lines[0]) + ("\n" + end if end and len(lines) > 1 else "")
    if kind == "tool" and "\nArguments: " in text:
        return retain(
            "\n".join(
                line
                for index, line in enumerate(text.splitlines())
                if index == 0 or line.startswith("Error: ")
            ),
            900,
        )
    # Code blocks are source evidence, not prose to replay through the default log.
    compact = re.sub(
        r"(?m)^\s*(```|~~~)[^\n]*\n(.*?)(?:^\s*\1[^\n]*$|\Z)",
        "[Code block collapsed]",
        text,
        flags=re.DOTALL,
    )
    lines = compact.splitlines(keepends=True)
    if len(lines) > 12:
        compact = "".join(lines[:6]) + OMISSION + "".join(lines[-6:])
    return retain(compact, 900)
