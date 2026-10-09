"""Independent storage and display bounds for public worker output."""

import re
import shlex


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
