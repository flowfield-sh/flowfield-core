"""Public workspace commands and explicit offline storage maintenance."""

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode

import typer
import uvicorn
from rich.console import Console
from rich.table import Table
from rich.text import Text

from flowfield import __version__, cli_views
from flowfield.client import Client
from flowfield.errors import ApplicationError
from flowfield.project_config import discover_project
from flowfield.state import data_path

app = typer.Typer(no_args_is_help=True, help="A local workspace behind your coding conversation.")
project_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
milestone_app = typer.Typer(no_args_is_help=True)
inbox_app = typer.Typer(no_args_is_help=True)
integration_app = typer.Typer(no_args_is_help=True)
harness_app = typer.Typer(no_args_is_help=True)
app.add_typer(project_app, name="project", help="Adopt existing projects and update their details.")
app.add_typer(task_app, name="task", help="Capture, prioritize and track tasks.")
app.add_typer(inbox_app, name="inbox", help="Read questions, answer them, and apply decisions.")
app.add_typer(
    milestone_app, name="milestone", help="Group related tasks without gates or dependencies."
)
app.add_typer(integration_app, name="integration", help="Connect and check coding harnesses.")
app.add_typer(harness_app, name="harness", help="Configure and check native coding harnesses.")
Json = Annotated[bool, typer.Option("--json", help="Emit clean JSON; operation errors use stderr.")]


def show_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", callback=show_version, is_eager=True)
    ] = False,
    data_dir: Annotated[Path | None, typer.Option(envvar="FLOWFIELD_DATA_DIR")] = None,
    port: Annotated[int, typer.Option(min=1, max=65535, envvar="FLOWFIELD_PORT")] = 8765,
) -> None:
    ctx.obj = Client((data_dir or data_path()).expanduser().resolve(), port)


def output(
    action: Callable[[], Any],
    json_output: bool,
    human: Callable[[Any], None] | None = None,
) -> None:
    try:
        result = action()
    except (ApplicationError, OSError, UnicodeError) as error:
        code = error.code if isinstance(error, ApplicationError) else "file_error"
        message = str(error)
        typer.echo(
            json.dumps({"error": {"code": code, "message": message}}) if json_output else message,
            err=True,
        )
        raise typer.Exit(1) from error
    if json_output:
        typer.echo(json.dumps(result))
    elif human is not None:
        human(result)
    else:
        display(result)


def connection_command(
    ctx: typer.Context, harness: str, name: str, operation: str, json_output: bool
) -> None:
    from flowfield.adapters.codex_connection import CodexConnection

    def run() -> dict[str, Any]:
        if harness != "codex":
            raise ApplicationError(
                "unsupported_harness", "Only codex connections are supported yet."
            )
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) is None:
            raise ApplicationError(
                "invalid_connection_name",
                "Connection names use 1–64 lowercase letters, numbers, hyphens or underscores.",
            )
        connection = CodexConnection(ctx.obj.port, name)
        return {
            "connect": connection.connect,
            "doctor": connection.doctor,
            "disconnect": connection.disconnect,
        }[operation]()

    def display(result: dict[str, Any]) -> None:
        typer.echo(result["message"])
        if "url" in result:
            typer.echo(
                f"Codex server: {name}\nEndpoint: {result['url']}\n"
                f"MCP tools verified: {len(result['tools'])}"
            )
        if operation == "connect":
            typer.echo("In Codex, ask: Use Flowfield to list my projects.")

    output(run, json_output, display)


@harness_app.command("status")
def harness_status(ctx: typer.Context, harness: str, json_output: Json = False) -> None:
    """Inspect detected native paths offline without starting an agent."""
    from flowfield.adapters.harness_host import status
    from flowfield.harness_models import HarnessKind
    from flowfield.harness_settings import KINDS, offline_registration

    def run() -> dict[str, Any]:
        if harness not in KINDS:
            raise ApplicationError("unsupported_harness", "This harness is not supported.")
        kind: HarnessKind = "codex" if harness == "codex" else "claude-code"
        return status(
            ctx.obj.directory, offline_registration(ctx.obj.directory, kind), os.environ
        ).model_dump()

    output(run, json_output, lambda value: cli_views.harness_status(value, ctx.obj.port))


@harness_app.command("settings")
def harness_settings(ctx: typer.Context, harness: str, json_output: Json = False) -> None:
    """Read service-host paths and readiness from the running service."""
    from flowfield.harness_settings import KINDS

    def run() -> Any:
        if harness not in KINDS:
            raise ApplicationError("unsupported_harness", "This harness is not supported.")
        return ctx.obj.request("GET", "harnesses/" + harness)

    output(run, json_output, lambda value: cli_views.harness_status(value, ctx.obj.port))


@harness_app.command("confirm-stopped")
def harness_confirm_stopped(
    ctx: typer.Context,
    harness: str,
    discovery: Annotated[
        str, typer.Option(help="Exact uncertain discovery ID from harness settings.")
    ],
    json_output: Json = False,
) -> None:
    """Confirm you stopped remaining native discovery on the host; starts no process."""
    from flowfield.harness_settings import KINDS

    def run() -> Any:
        if harness not in KINDS:
            raise ApplicationError("unsupported_harness", "This harness is not supported.")
        return ctx.obj.request(
            "POST", f"harnesses/{harness}/catalog/confirm-stopped", {"id": discovery}
        )

    output(
        run,
        json_output,
        lambda _: typer.echo(f"Confirmed discovery {discovery} stopped for {harness}."),
    )


@harness_app.command("configure")
def harness_configure(
    ctx: typer.Context,
    harness: str,
    revision: Annotated[int, typer.Option("--revision", min=1)],
    executable: Annotated[
        str | None, typer.Option(help="Absolute native executable on the service host.")
    ] = None,
    config_directory: Annotated[
        str | None, typer.Option(help="Native configuration directory, not a config file.")
    ] = None,
    json_output: Json = False,
) -> None:
    """Save host overrides; omitting both paths restores native defaults. No native files change."""
    from flowfield.harness_settings import KINDS

    def run() -> Any:
        if harness not in KINDS:
            raise ApplicationError("unsupported_harness", "This harness is not supported.")
        return ctx.obj.request(
            "PUT",
            "harnesses/" + harness,
            {
                "expected_revision": revision,
                "executable": executable,
                "config_directory": config_directory,
            },
        )

    output(
        run,
        json_output,
        lambda value: typer.echo(
            f"Saved {harness} host settings · revision {value['revision']}\n"
            f"Executable override: {executable or 'Automatic detection'}\n"
            f"Configuration override: {config_directory or 'Native default'}"
        ),
    )


@integration_app.command()
def connect(
    ctx: typer.Context,
    harness: str,
    name: Annotated[str, typer.Option(help="Codex MCP connection name.")] = "flowfield",
    json_output: Json = False,
) -> None:
    """Connect Codex to the running service; preserve existing harness settings."""
    connection_command(ctx, harness, name, "connect", json_output)


@integration_app.command("status")
def doctor(
    ctx: typer.Context,
    harness: str,
    name: Annotated[str, typer.Option(help="Codex MCP connection name.")] = "flowfield",
    json_output: Json = False,
) -> None:
    """Check harness configuration and discover service tools without a model call."""
    connection_command(ctx, harness, name, "doctor", json_output)


@integration_app.command()
def disconnect(
    ctx: typer.Context,
    harness: str,
    name: Annotated[str, typer.Option(help="Codex MCP connection name.")] = "flowfield",
    json_output: Json = False,
) -> None:
    """Remove this service's Codex connection; retain projects and tasks."""
    connection_command(ctx, harness, name, "disconnect", json_output)


@app.command()
def version(json_output: Json = False) -> None:
    """Report the installed version without contacting a service."""
    if json_output:
        typer.echo(json.dumps({"version": __version__}))
    else:
        Console().print(f"Flowfield [bold]{__version__}[/bold]")


@app.command()
def serve(
    ctx: typer.Context,
    port: Annotated[int | None, typer.Option(min=1, max=65535, help="Local service port.")] = None,
    open_browser: Annotated[
        bool,
        typer.Option("--open/--no-open", help="Open the browser from an interactive terminal."),
    ] = True,
) -> None:
    """Serve the API and built admin panel on 127.0.0.1. Stop with Ctrl-C."""
    from flowfield.api import create_app
    from flowfield.serve import BrowserServer

    client: Client = ctx.obj
    BrowserServer(
        uvicorn.Config(
            create_app(data_dir=client.directory), host="127.0.0.1", port=port or client.port
        ),
        open_browser=open_browser,
    ).run()


@project_app.command("init")
def init(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="Existing project directory.")] = Path("."),
    id: Annotated[str | None, typer.Option()] = None,
    name: Annotated[str | None, typer.Option()] = None,
    prefix: Annotated[str | None, typer.Option(help="Unique three-letter task prefix.")] = None,
    json_output: Json = False,
) -> None:
    """Register a project and create its portable .flowfield/config.toml."""
    output(
        lambda: ctx.obj.request(
            "POST",
            "projects/initialize",
            {
                "path": str(path.expanduser().resolve()),
                "id": id,
                "name": name,
                "task_prefix": prefix,
            },
        ),
        json_output,
    )


@project_app.command("guidance")
def project_guidance(
    ctx: typer.Context,
    action: Annotated[
        str, typer.Argument(help="show, preview, export, install, or remove")
    ] = "show",
    project: Annotated[
        str | None, typer.Option(help="Project ID; discovered from cwd if omitted.")
    ] = None,
    part: Annotated[
        str, typer.Option(help="Export agents section or coordinator skill.")
    ] = "coordinator",
    expected_revision: Annotated[str | None, typer.Option()] = None,
    json_output: Json = False,
) -> None:
    """Preview or deliberately adopt local guidance; preserve existing instructions."""
    from flowfield.guidance import template

    def run() -> Any:
        if action == "export":
            if part not in ("agents", "coordinator"):
                raise ApplicationError("invalid_request", "Choose --part agents or coordinator.")
            return template(
                "agents-section.md" if part == "agents" else "flowfield-coordinator/SKILL.md"
            )
        if action not in ("show", "preview", "install", "remove"):
            raise ApplicationError(
                "invalid_request", "Choose show, preview, export, install or remove."
            )
        path = project_path(project) + "/guidance"
        current = ctx.obj.request("GET", path)
        if action in ("install", "remove"):
            current = ctx.obj.request(
                "POST",
                path,
                {"action": action, "expected_revision": expected_revision or current["revision"]},
                compact=json_output,
            )
        return current

    def human(value: Any) -> None:
        if isinstance(value, str):
            typer.echo(value, nl=False)
            return
        typer.echo(value.get("message") or f"Project guidance: {value['status']}")
        for heading, items in (
            ("Changed files", value.get("changed_files", [])),
            ("Review", value["notices"]),
            ("Next", value.get("next_steps", []) if action != "remove" else []),
        ):
            if items:
                typer.echo(f"\n{heading}:")
                for item in items:
                    typer.echo(f"- {item}")
        if action in ("show", "preview"):
            typer.echo(f"\nWorker baseline: {value['baseline']} ({value['baseline_ref']})")
            if value["instruction_files"]:
                typer.echo("Instruction files: " + ", ".join(value["instruction_files"]))
        if action == "preview":
            typer.echo("\nAGENTS.md section:\n" + value["section"])
            typer.echo(".agents/skills/flowfield-coordinator/SKILL.md:\n" + value["skill"])

    output(run, json_output, human)


@project_app.command("list")
def project_list(
    ctx: typer.Context, after: str | None = None, limit: int = 20, json_output: Json = False
) -> None:
    output(
        lambda: ctx.obj.request(
            "GET", "context/projects?" + urlencode({"after": after or "", "limit": limit})
        ),
        json_output,
    )


ProjectOption = Annotated[
    str | None, typer.Option(help="Project ID; defaults to local project config.")
]
Expected = Annotated[
    int | None,
    typer.Option(
        min=1, help="Reject a changed record; default reads current revision before editing."
    ),
]
Author = Annotated[str, typer.Option(help="Attribution label, not authenticated identity.")]


def label(value: str) -> str:
    return value.replace("_", " ").capitalize()


def readiness_text(task: dict[str, Any]) -> str:
    if task.get("readiness") == "needs_reconciliation" and not task.get("reconciliation_reason"):
        return "Changed requirements need reconciliation"
    if task.get("reconciliation_reason"):
        return f"Needs update: {task['reconciliation_reason']}"
    if task.get("blocking_question_count") or task.get("blocking_questions"):
        return "Awaiting input application; use inbox list for the questions"
    if task.get("blocked_count") and not task.get("blocked_by"):
        return f"Blocked by {task['blocked_count']} task(s); use task show for details"
    if task.get("blocked_by"):
        return "Blocked by: " + ", ".join(f"{p['key']} ({p['title']})" for p in task["blocked_by"])
    if task.get("status") in ("backlog", "up_next") and task.get("publication_status") == "draft":
        return "Draft; coordinator must publish before work starts"
    return (
        "Published; prerequisites satisfied"
        if task.get("publication_status") == "published"
        else "Recorded prerequisites satisfied"
    )


def display(result: Any) -> None:
    console = Console()
    if isinstance(result, dict) and result.get("saved"):
        if "kind" in result:
            typer.echo(f"Saved {result['kind']} {result['id']} · {result.get('author', 'human')}")
        else:
            typer.echo(f"Saved {result.get('key', result['id'])} · Revision {result['revision']}")
    elif isinstance(result, dict) and "text" in result:
        typer.echo(result["text"])
        if result.get("output_omitted"):
            typer.echo("Some public output was omitted and cannot be recovered.")
        if result.get("next_offset") is not None:
            typer.echo(
                f"More: --offset {result['next_offset']}"
                + (
                    f" --revision {result['revision']}"
                    if result.get("revision") is not None
                    else ""
                )
            )
    elif (
        isinstance(result, dict)
        and "items" in result
        and (not result["items"] or "kind" not in result["items"][0])
    ):
        if result["items"] and "change_note" in result["items"][0]:
            for item in result["items"]:
                typer.echo(
                    f"Revision {item['revision']} · {item['updated_by']} · "
                    f"{item['updated_at']} · {item['title']}"
                )
        else:
            display(result["items"])
        if result.get("next_cursor") is not None:
            flag = "before" if result["items"] and "change_note" in result["items"][0] else "after"
            typer.echo(f"More available: --{flag} {result['next_cursor']}")
    elif isinstance(result, dict) and ("items" in result or "sequence" in result):
        entries = result.get("items", [result])
        if not entries:
            typer.echo("No activity yet.")
        for entry in entries:
            state = (
                " · Withdrawn"
                if entry.get("withdrawn_by")
                else " · Superseded"
                if entry["superseded_by"]
                else ""
            )
            typer.echo(f"{label(entry['kind'])}{state} · {entry['author']} · {entry['created_at']}")
            typer.echo(f"{entry['body']}\nID: {entry['id']}")
            if entry.get("truncated_fields"):
                typer.echo(f"Excerpt only; project entry {entry['id']} reads the full text.")
            if entry["supersedes"]:
                typer.echo(f"Supersedes: {entry['supersedes']}")
            if entry["superseded_by"]:
                typer.echo(f"Superseded by: {entry['superseded_by']}")
            typer.echo("")
        if result.get("next_cursor"):
            typer.echo(f"More available: --before {result['next_cursor']}")
    elif isinstance(result, list):
        if not result:
            typer.echo("No items yet.")
            return
        is_tasks = "status" in result[0]
        table = Table(
            "Key" if "key" in result[0] else "ID",
            "Title" if "title" in result[0] else "Project",
            *(["Type", "Column", "Milestone", "Readiness"] if is_tasks else []),
        )
        for item in result:
            fields = [item.get("key", item["id"]), item.get("title", item.get("name", ""))]
            if is_tasks:
                fields += [
                    label(item["task_type"]),
                    "Archived" if item["archived"] else label(item["status"]),
                    item["milestone_id"] or "—",
                    readiness_text(item),
                ]
            table.add_row(*(Text(field) for field in fields))
        console.print(table)
    elif "project" in result:
        project = result["project"]
        typer.echo(f"{project['name']}\nDescription: {project['description'] or 'Not set'}")
        if result.get("needs_you_count") or result.get("awaiting_application_count"):
            typer.echo(
                f"Needs you: {result.get('needs_you_count', 0)} · "
                f"Coordinator input: {result.get('awaiting_application_count', 0)}"
            )
        for column in result["columns"]:
            tasks = column["tasks"]
            typer.echo(f"\n{label(column['status'])} ({column['count']})")
            for task in tasks:
                typer.echo(f"  {task['key']}  {task['title']} ({task['task_type']})")
                typer.echo(f"    {readiness_text(task)}")
            if column["has_more"]:
                typer.echo(
                    f"  Showing {len(tasks)}; task list --status {column['status']} for more."
                )
        if project.get("truncated_fields"):
            typer.echo("Project text is excerpted; use project text for the full description.")
        typer.echo(
            "\nUp next expresses priority. "
            "Readiness covers publication, prerequisites and blocking questions; "
            "an enabled queue starts eligible published work."
        )
        if result.get("recommendation"):
            item = result["recommendation"]
            typer.echo(
                f"\nSuggested next: {label(item['action'])} "
                f"{item.get('task_key', item.get('id', ''))} — {item['reason']}"
            )
            for status, heading in (("open", "Needs you"), ("answered", "Answers sent")):
                group = result["attention"][status]
                typer.echo(f"{heading}: {group['count']}")
                for question in group["items"]:
                    typer.echo(f"  {question['id']}: {question['question']}")
                    if delivery := question.get("delivery"):
                        typer.echo(f"    {delivery['state']}: {delivery['message']}")
            typer.echo(f"Observed: {result['observed_at']}")
    elif "title" in result:
        typer.echo(f"{result['title']}  [{result.get('key', result['id'])}]")
        if "status" in result:
            typer.echo(
                f"{label(result['task_type'])} · {label(result['status'])}"
                + (" · Archived" if result["archived"] else "")
            )
            typer.echo(f"Milestone: {result['milestone_id'] or 'None'}")
            if result.get("publication_status"):
                publication_labels = {
                    "published": "Published",
                    "draft": "Draft",
                    "needs_reconciliation": "Needs update",
                }
                typer.echo(f"Publication: {publication_labels[result['publication_status']]}")
            typer.echo(readiness_text(result))
            if result.get("dependencies"):
                typer.echo("Prerequisites: " + ", ".join(t["key"] for t in result["prerequisites"]))
            if result.get("dependents"):
                typer.echo("Needed by: " + ", ".join(t["key"] for t in result["dependents"]))
            for relation in ("prerequisites", "blocked_by", "dependents"):
                shown = len(result.get(relation, []))
                total = result.get(relation + "_count", shown)
                if total > shown:
                    typer.echo(
                        f"{relation}: showing {shown} of {total}; "
                        f"task relationships {result['key']} --relation {relation} for more."
                    )
        if result.get("body"):
            typer.echo(result["body"])
        if result.get("handoff"):
            handoff = result["handoff"]
            typer.echo(
                f"\nHandoff: {handoff['id']}"
                + (" · Needs recheck" if handoff["needs_recheck"] else "")
            )
            typer.echo(handoff["body"])
            if handoff.get("truncated_fields"):
                typer.echo(f"Excerpt only; project entry {handoff['id']} reads the full text.")
        typer.echo(
            f"\nRevision {result['revision']} · {result['updated_by']} · {result['updated_at']}"
        )
    else:
        typer.echo(f"{result['name']}  [{result['id']}]\n{result['path']}")
        typer.echo(f"Task prefix: {result['task_prefix']}")
        typer.echo(f"Description: {result['description'] or 'Not set'}")

    if isinstance(result, dict) and result.get("truncated_fields"):
        typer.echo(
            "Excerpt only: "
            + ", ".join(result["truncated_fields"])
            + ". Use text/entry to continue."
        )


def project_path(project: str | None) -> str:
    identity = project if project is not None else discover_project(Path.cwd())
    return f"projects/{quote(identity, safe='')}"


def item_path(project: str | None, kind: str, identity: str | None = None) -> str:
    path = f"{project_path(project)}/{kind}"
    return path if identity is None else f"{path}/{quote(identity, safe='')}"


def text_value(text: str | None, file: Path | None) -> str | None:
    if text is not None and file is not None:
        raise ApplicationError("invalid_request", "Use inline text or its file option, not both.")
    return file.read_text(encoding="utf-8") if file is not None else text


def stages_value(file: Path) -> Any:
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ApplicationError("invalid_request", "Stages file must contain valid JSON.") from error


def update(
    client: Client,
    path: str,
    changes: dict[str, Any],
    expected: int | None,
    author: str,
    *,
    operation: str = "",
) -> Any:
    revision = (
        expected if expected is not None else client.request("GET", "context/" + path)["revision"]
    )
    return client.request(
        "POST" if operation else "PUT",
        path + (f"/{operation}" if operation else ""),
        {**changes, "expected_revision": revision, "author": author},
    )


@project_app.command("show")
def project_show(
    ctx: typer.Context, project: ProjectOption = None, json_output: Json = False
) -> None:
    """Show the project selected explicitly or from local configuration."""
    output(lambda: ctx.obj.request("GET", "context/" + project_path(project)), json_output)


@project_app.command("edit")
def project_edit(
    ctx: typer.Context,
    project: ProjectOption = None,
    name: str | None = None,
    prefix: Annotated[
        str | None, typer.Option(help="Three letters; editable before the first task.")
    ] = None,
    description: str | None = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Update supplied name or description."""
    output(
        lambda: update(
            ctx.obj,
            project_path(project),
            {
                k: v
                for k, v in {
                    "name": name,
                    "description": description,
                    "task_prefix": prefix,
                }.items()
                if v is not None
            },
            expected_revision,
            author,
        ),
        json_output,
    )


@app.command("status")
def status(ctx: typer.Context, project: ProjectOption = None, json_output: Json = False) -> None:
    """Show project details, ordered work and recorded prerequisite readiness."""
    output(
        lambda: ctx.obj.request("GET", "context/" + project_path(project) + "/board"), json_output
    )


@project_app.command("search")
def search_context(
    ctx: typer.Context,
    query: str,
    project: ProjectOption = None,
    entity: str | None = None,
    kind: str | None = None,
    task: str | None = None,
    history: str = "current",
    since: str | None = None,
    until: str | None = None,
    before: int | None = None,
    limit: int = 20,
    json_output: Json = False,
) -> None:
    """Find source-linked evidence; historical matches never replace current intent."""
    from urllib.parse import urlencode

    parameters = {
        key: value
        for key, value in {
            "query": query,
            "entity": entity,
            "kind": kind,
            "task_id": task,
            "history": history,
            "since": since,
            "until": until,
            "before": before,
            "limit": limit,
        }.items()
        if value is not None
    }
    output(
        lambda: ctx.obj.request(
            "GET", "context/" + project_path(project) + "/search?" + urlencode(parameters)
        ),
        json_output,
        cli_views.search,
    )


@task_app.command("list")
def task_list(
    ctx: typer.Context,
    project: ProjectOption = None,
    include_archived: bool = False,
    status: str | None = None,
    task_type: Annotated[str | None, typer.Option("--type")] = None,
    milestone: str | None = None,
    readiness: str | None = None,
    query: str | None = None,
    after: int | None = None,
    limit: int = 20,
    json_output: Json = False,
) -> None:
    """Page task summaries by stable task number; filter before opening details."""
    params = {
        k: v
        for k, v in {
            "include_archived": str(include_archived).lower(),
            "status": status.replace("-", "_") if status else None,
            "task_type": task_type,
            "milestone_id": milestone,
            "readiness": readiness,
            "query": query,
            "after": after,
            "limit": limit,
        }.items()
        if v is not None
    }
    output(
        lambda: ctx.obj.request(
            "GET", "context/" + item_path(project, "tasks") + "?" + urlencode(params)
        ),
        json_output,
    )


@task_app.command("show")
def task_show(
    ctx: typer.Context, task: str, project: ProjectOption = None, json_output: Json = False
) -> None:
    """Read current agreement and bounded relationships; history is a separate command."""
    output(
        lambda: ctx.obj.request("GET", "context/" + item_path(project, "tasks", task)), json_output
    )


@task_app.command("create")
def task_create(
    ctx: typer.Context,
    title: Annotated[str, typer.Option()],
    id: str | None = None,
    body: str | None = None,
    body_file: Path | None = None,
    task_type: Annotated[
        str, typer.Option("--type", help="feature, bug, maintenance or investigation")
    ] = "feature",
    status: str = "backlog",
    milestone: str | None = None,
    depends_on: Annotated[
        list[str] | None,
        typer.Option("--depends-on", help="Prerequisite task ID; repeat for multiple tasks."),
    ] = None,
    stages_file: Annotated[Path | None, typer.Option(help="JSON stages array.")] = None,
    project: ProjectOption = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Capture a task. IDs are generated by default; descriptions accept inline text or files."""
    output(
        lambda: ctx.obj.request(
            "POST",
            item_path(project, "tasks"),
            {
                "title": title,
                "id": id,
                "stages": stages_value(stages_file)
                if stages_file
                else [{"id": "work", "title": title[:80], "outcome": title}],
                "body": text_value(body, body_file) or "",
                "task_type": task_type,
                "status": status.replace("-", "_"),
                "milestone_id": milestone,
                "dependencies": depends_on or [],
                "author": author,
            },
        ),
        json_output,
    )


@task_app.command("stages")
def task_stages(
    ctx: typer.Context, task: str, project: ProjectOption = None, json_output: Json = False
) -> None:
    """Read the current plan and revision before editing or preparing a task."""
    output(
        lambda: ctx.obj.request("GET", item_path(project, "tasks", task) + "/stages"),
        json_output,
        cli_views.stages,
    )


@task_app.command("edit")
def task_edit(
    ctx: typer.Context,
    task: str,
    title: str | None = None,
    body: str | None = None,
    body_file: Path | None = None,
    task_type: Annotated[str | None, typer.Option("--type")] = None,
    milestone: str | None = None,
    no_milestone: bool = False,
    depends_on: Annotated[
        list[str] | None,
        typer.Option("--depends-on", help="Replace prerequisites; repeat for multiple tasks."),
    ] = None,
    no_dependencies: Annotated[bool, typer.Option(help="Remove all prerequisites.")] = False,
    stages_file: Annotated[
        Path | None, typer.Option(help="JSON stages change: expected_revision, stages, reason.")
    ] = None,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Edit supplied fields; --no-milestone removes grouping.

    Use prioritize for upcoming order and progress to record coordinated work.
    """

    def run() -> Any:
        if milestone is not None and no_milestone:
            raise ApplicationError(
                "invalid_request", "Use --milestone or --no-milestone, not both."
            )
        changes: dict[str, Any] = {
            k: v
            for k, v in {
                "title": title,
                "body": text_value(body, body_file),
                "task_type": task_type,
                "milestone_id": milestone,
            }.items()
            if v is not None
        }
        if stages_file is not None:
            changes["stages"] = stages_value(stages_file)
        if no_milestone:
            changes["milestone_id"] = None
        if depends_on is not None and no_dependencies:
            raise ApplicationError(
                "invalid_request", "Use --depends-on or --no-dependencies, not both."
            )
        if depends_on is not None or no_dependencies:
            changes["dependencies"] = depends_on or []
        return update(
            ctx.obj, item_path(project, "tasks", task), changes, expected_revision, author
        )

    output(run, json_output)


@task_app.command("prioritize")
def task_prioritize(
    ctx: typer.Context,
    task: str,
    status: str,
    before: Annotated[
        str | None,
        typer.Option(help="Place before this task in the destination column; default appends."),
    ] = None,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Choose backlog/up-next and relative priority; never starts work."""
    output(
        lambda: update(
            ctx.obj,
            item_path(project, "tasks", task),
            {"status": status.replace("-", "_"), "before_id": before},
            expected_revision,
            author,
            operation="prioritize",
        ),
        json_output,
    )


@task_app.command("prepare")
def task_prepare(
    ctx: typer.Context,
    task: str,
    completion: Annotated[str, typer.Option(help="Completion requirement: code or report.")],
    stages_file: Annotated[
        Path | None, typer.Option(help="JSON stages change: expected_revision, stages, reason.")
    ] = None,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "coordinator",
    json_output: Json = False,
) -> None:
    """Prepare an existing assignment after checking requirements.

    Clarify gaps first. This does not start work or accept code.
    """

    def run() -> Any:
        path = item_path(project, "tasks", task)
        current = ctx.obj.request("GET", "context/" + path)
        return update(
            ctx.obj,
            path,
            {
                **({"stages": stages_value(stages_file)} if stages_file else {}),
                "preparation": {"completion": completion},
            },
            expected_revision if expected_revision is not None else current["revision"],
            author,
        )

    output(run, json_output)


@task_app.command("reconcile")
def task_reconcile(
    ctx: typer.Context,
    task: str,
    note: Annotated[str, typer.Option(help="Explain the review that validates the affected work.")],
    completion: Annotated[
        str | None,
        typer.Option(help="Explicitly rebind code/report completion; code uses configured target."),
    ] = None,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Validate affected recorded work once its prerequisites are satisfied."""
    output(
        lambda: update(
            ctx.obj,
            item_path(project, "tasks", task),
            {
                "note": note,
                **({"completion": completion} if completion else {}),
            },
            expected_revision,
            author,
            operation="reconcile",
        ),
        json_output,
    )


@task_app.command("progress")
def task_progress(
    ctx: typer.Context,
    task: str,
    status: str,
    completion: Annotated[
        str | None, typer.Option(help="Use report for manual report acceptance.")
    ] = None,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Record coordinated work state (including reopening); does not launch or stop a worker."""
    output(
        lambda: update(
            ctx.obj,
            item_path(project, "tasks", task),
            {
                "status": status.replace("-", "_"),
                **({"completion": completion} if completion else {}),
            },
            expected_revision,
            author,
            operation="progress",
        ),
        json_output,
    )


def archive_task(
    ctx: typer.Context,
    task: str,
    project: str | None,
    expected: int | None,
    author: str,
    json_output: bool,
    archived: bool,
) -> None:
    output(
        lambda: update(
            ctx.obj, item_path(project, "tasks", task), {"archived": archived}, expected, author
        ),
        json_output,
    )


@task_app.command("archive")
def task_archive(
    ctx: typer.Context,
    task: str,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Archive abandoned work, preserving its column and history."""
    archive_task(ctx, task, project, expected_revision, author, json_output, True)


@task_app.command("restore")
def task_restore(
    ctx: typer.Context,
    task: str,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Return an archived task to the end of its preserved column."""
    archive_task(ctx, task, project, expected_revision, author, json_output, False)


@milestone_app.command("list")
def milestone_list(
    ctx: typer.Context,
    project: ProjectOption = None,
    after: str | None = None,
    limit: int = 20,
    json_output: Json = False,
) -> None:
    output(
        lambda: ctx.obj.request(
            "GET",
            "context/"
            + item_path(project, "milestones")
            + "?"
            + urlencode({"after": after or "", "limit": limit}),
        ),
        json_output,
    )


@milestone_app.command("show")
def milestone_show(
    ctx: typer.Context, milestone: str, project: ProjectOption = None, json_output: Json = False
) -> None:
    output(
        lambda: ctx.obj.request("GET", "context/" + item_path(project, "milestones", milestone)),
        json_output,
    )


@milestone_app.command("create")
def milestone_create(
    ctx: typer.Context,
    title: Annotated[str, typer.Option()],
    id: str | None = None,
    body: str | None = None,
    body_file: Path | None = None,
    project: ProjectOption = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Create an optional grouping. Tasks can also stand alone."""
    output(
        lambda: ctx.obj.request(
            "POST",
            item_path(project, "milestones"),
            {"id": id, "title": title, "body": text_value(body, body_file) or "", "author": author},
        ),
        json_output,
    )


@milestone_app.command("edit")
def milestone_edit(
    ctx: typer.Context,
    milestone: str,
    title: str | None = None,
    body: str | None = None,
    body_file: Path | None = None,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Edit a milestone's title or description without changing its members."""
    output(
        lambda: update(
            ctx.obj,
            item_path(project, "milestones", milestone),
            {
                k: v
                for k, v in {"title": title, "body": text_value(body, body_file)}.items()
                if v is not None
            },
            expected_revision,
            author,
        ),
        json_output,
    )


def read_activity(
    ctx: typer.Context,
    project: str | None,
    task: str | None,
    current: bool,
    before: int | None,
    limit: int,
    json_output: bool,
) -> None:
    from urllib.parse import urlencode

    query: dict[str, Any] = {"limit": limit, "current_only": str(current).lower()}
    if task is not None:
        query["task_id"] = task
    if before is not None:
        query["before"] = before
    output(
        lambda: ctx.obj.request(
            "GET", "context/" + project_path(project) + "/activity?" + urlencode(query)
        ),
        json_output,
    )


@task_app.command("activity")
def task_activity(
    ctx: typer.Context,
    task: str,
    project: ProjectOption = None,
    current: bool = False,
    before: int | None = None,
    limit: int = 20,
    json_output: Json = False,
) -> None:
    """Read task activity; --current excludes superseded handoffs."""
    read_activity(ctx, project, task, current, before, limit, json_output)


def append_activity(
    ctx: typer.Context,
    task: str | None,
    project: str | None,
    kind: str,
    body: str | None,
    body_file: Path | None,
    supersedes: str | None,
    entry_id: str | None,
    author: str,
    json_output: bool,
) -> None:
    output(
        lambda: ctx.obj.request(
            "POST",
            project_path(project) + "/activity",
            {
                "task_id": task,
                "kind": kind,
                "body": text_value(body, body_file) or "",
                "supersedes": supersedes,
                "author": author,
                **({"id": entry_id} if entry_id is not None else {}),
            },
        ),
        json_output,
    )


@task_app.command("note")
def task_note(
    ctx: typer.Context,
    task: str,
    body: str | None = None,
    body_file: Path | None = None,
    project: ProjectOption = None,
    id: str | None = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Append a Markdown finding, progress update or result; never steers work."""
    append_activity(ctx, task, project, "note", body, body_file, None, id, author, json_output)


@task_app.command("handoff")
def task_handoff(
    ctx: typer.Context,
    task: str,
    expected_revision: Annotated[int, typer.Option(min=1)],
    supersedes: Annotated[str, typer.Option(help="Selected handoff ID, or 'none' for the first.")],
    body: str | None = None,
    body_file: Path | None = None,
    project: ProjectOption = None,
    id: str | None = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Save a continuation checkpoint: work location, actual checks, limitations and next step."""
    output(
        lambda: ctx.obj.request(
            "POST",
            project_path(project) + "/activity",
            {
                "task_id": task,
                "kind": "handoff",
                "body": text_value(body, body_file) or "",
                "expected_task_revision": expected_revision,
                "supersedes": None if supersedes == "none" else supersedes,
                "author": author,
                **({"id": id} if id else {}),
            },
        ),
        json_output,
    )


@task_app.command("revisions")
def task_revisions(
    ctx: typer.Context,
    task: str,
    project: ProjectOption = None,
    before: int | None = None,
    limit: int = 20,
    json_output: Json = False,
) -> None:
    """Page revision metadata; task text --revision reads saved agreement fields."""
    query = {k: v for k, v in {"before": before, "limit": limit}.items() if v is not None}
    output(
        lambda: ctx.obj.request(
            "GET", "context/" + item_path(project, "tasks", task) + "/revisions?" + urlencode(query)
        ),
        json_output,
    )


@task_app.command("relationships")
def task_relationships(
    ctx: typer.Context,
    task: str,
    project: ProjectOption = None,
    relation: str = "prerequisites",
    after: int | None = None,
    limit: int = 20,
    json_output: Json = False,
) -> None:
    """Page prerequisites, blocked_by or dependents; task show previews five of each."""
    query = {
        k: v
        for k, v in {"relation": relation, "after": after, "limit": limit}.items()
        if v is not None
    }
    output(
        lambda: ctx.obj.request(
            "GET",
            "context/" + item_path(project, "tasks", task) + "/relationships?" + urlencode(query),
        ),
        json_output,
    )


@task_app.command("text")
def task_text(
    ctx: typer.Context,
    task: str,
    project: ProjectOption = None,
    field: str = "body",
    revision: int | None = None,
    offset: int = 0,
    limit: int = 4000,
    json_output: Json = False,
) -> None:
    """Read full task fields deliberately in chunks; pass returned revision on continuation."""
    read_text(ctx, "task", task, project, field, revision, offset, limit, json_output)


@project_app.command("text")
def project_text(
    ctx: typer.Context,
    project: ProjectOption = None,
    field: str = "description",
    revision: int | None = None,
    offset: int = 0,
    limit: int = 4000,
    json_output: Json = False,
) -> None:
    """Read full description/path in chunks."""
    read_text(ctx, "project", None, project, field, revision, offset, limit, json_output)


@milestone_app.command("text")
def milestone_text(
    ctx: typer.Context,
    milestone: str,
    project: ProjectOption = None,
    revision: int | None = None,
    offset: int = 0,
    limit: int = 4000,
    json_output: Json = False,
) -> None:
    """Read a full milestone description in chunks."""
    read_text(ctx, "milestone", milestone, project, "body", revision, offset, limit, json_output)


@project_app.command("entry")
def activity_entry(
    ctx: typer.Context,
    entry: str,
    project: ProjectOption = None,
    offset: int = 0,
    limit: int = 4000,
    json_output: Json = False,
) -> None:
    """Read a project or task activity entry's full text in chunks by entry ID."""
    read_text(ctx, "activity", entry, project, "body", None, offset, limit, json_output)


@project_app.command("coordinator-history")
def coordinator_history(
    ctx: typer.Context,
    project: ProjectOption = None,
    before: int | None = None,
    limit: int = 20,
    json_output: Json = False,
) -> None:
    """Page saved public coordinator exchanges, newest first."""
    query = urlencode(
        {k: v for k, v in {"before": before, "limit": limit}.items() if v is not None}
    )
    output(
        lambda: ctx.obj.request(
            "GET", "context/" + project_path(project) + "/coordinator-history?" + query
        ),
        json_output,
        display_coordinator_history,
    )


def display_coordinator_history(result: dict[str, Any]) -> None:
    for item in result["items"]:
        typer.echo(f"Exchange {item['number']} · {item['status']} · {item['id']}")
        typer.echo(f"Human: {item['human']}\nCoordinator: {item['coordinator']}")
        if item.get("truncated_fields"):
            typer.echo("Excerpt; use project coordinator-text for retained text.")
        if item["output_omitted"]:
            typer.echo("Some public output was omitted and cannot be recovered.")
    if not result["items"]:
        typer.echo("No saved coordinator exchanges.")
    if result.get("next_cursor") is not None:
        typer.echo(f"More available: --before {result['next_cursor']}")


@project_app.command("coordinator-text")
def coordinator_text(
    ctx: typer.Context,
    turn: str,
    project: ProjectOption = None,
    field: str = "human",
    revision: int | None = None,
    offset: int = 0,
    limit: int = 4000,
    json_output: Json = False,
) -> None:
    """Read a saved exchange's human/coordinator text in chunks."""
    read_text(ctx, "coordinator", turn, project, field, revision, offset, limit, json_output)


def read_text(
    ctx: typer.Context,
    resource: str,
    identity: str | None,
    project: str | None,
    field: str,
    revision: int | None,
    offset: int,
    limit: int,
    json_output: bool,
) -> None:
    query = {
        k: v
        for k, v in {
            "resource": resource,
            "identity": identity,
            "field": field,
            "revision": revision,
            "offset": offset,
            "limit": limit,
        }.items()
        if v is not None
    }
    output(
        lambda: ctx.obj.request(
            "GET", "context/" + project_path(project) + "/text?" + urlencode(query)
        ),
        json_output,
    )


def display_inbox(result: dict[str, Any]) -> None:
    states = {
        "open": "Needs your answer",
        "answered": "Resume coordinator",
        "assigned": "Answer sent",
        "applied": "Answered",
        "withdrawn": "Withdrawn",
    }
    for question in result.get("items", [result]):
        delivery = question.get("delivery")
        state = delivery["state"] if delivery else states[question["status"]]
        typer.echo(f"{question['task_key'] or 'Project'} · {state} · {question['id']}")
        if delivery:
            typer.echo(delivery["message"])
        if question.get("affected_task_keys"):
            typer.echo("Affects: " + ", ".join(question["affected_task_keys"]))
        typer.echo(question["question"])
        for name in ("context", "recommendation", "blocking_scope", "answer", "decision"):
            if question.get(name):
                typer.echo(f"{label(name)}: {question[name]}")
        if question.get("truncated_fields"):
            typer.echo("Excerpt; use inbox text for complete fields.")
        typer.echo("")
    if result.get("items") == []:
        typer.echo("No questions in this view.")
    if result.get("next_cursor"):
        typer.echo(f"More: --after {result['next_cursor']}")


@inbox_app.command("list")
def inbox_list(
    ctx: typer.Context,
    project: ProjectOption = None,
    status: str = "active",
    task: str | None = None,
    after: int | None = None,
    limit: int = 20,
    json_output: Json = False,
) -> None:
    query = urlencode(
        {
            k: v
            for k, v in {"status": status, "task_id": task, "after": after, "limit": limit}.items()
            if v is not None
        }
    )
    output(
        lambda: ctx.obj.request("GET", "context/" + item_path(project, "questions") + "?" + query),
        json_output,
        display_inbox,
    )


@inbox_app.command("show")
def inbox_show(
    ctx: typer.Context,
    question: str,
    project: ProjectOption = None,
    revision: int | None = None,
    json_output: Json = False,
) -> None:
    output(
        lambda: ctx.obj.request(
            "GET",
            "context/"
            + item_path(project, "questions", question)
            + (f"?revision={revision}" if revision else ""),
        ),
        json_output,
        display_inbox,
    )


@inbox_app.command("text")
def inbox_text(
    ctx: typer.Context,
    question: str,
    field: str = "context",
    project: ProjectOption = None,
    revision: int | None = None,
    offset: int = 0,
    json_output: Json = False,
) -> None:
    query = urlencode(
        {
            k: v
            for k, v in {
                "resource": "question",
                "identity": question,
                "field": field,
                "revision": revision,
                "offset": offset,
            }.items()
            if v is not None
        }
    )
    output(
        lambda: ctx.obj.request("GET", "context/" + project_path(project) + "/text?" + query),
        json_output,
    )


@inbox_app.command("ask")
def inbox_ask(
    ctx: typer.Context,
    question: Annotated[str, typer.Option()],
    context: Annotated[str, typer.Option()],
    recommendation: Annotated[str, typer.Option()],
    task: Annotated[str | None, typer.Argument(help="Omit for a project question.")] = None,
    affects: Annotated[list[str] | None, typer.Option("--affects")] = None,
    blocking_scope: str | None = None,
    choice: Annotated[list[str] | None, typer.Option("--choice")] = None,
    id: str | None = None,
    project: ProjectOption = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    data = {
        "task_id": task,
        "affected_task_ids": affects or [],
        "question": question,
        "context": context,
        "recommendation": recommendation,
        "blocking_scope": blocking_scope,
        "choices": choice or [],
        "author": author,
    }
    if id is not None:
        data["id"] = id
    output(lambda: ctx.obj.request("POST", item_path(project, "questions"), data), json_output)


@inbox_app.command("answer")
def inbox_answer(
    ctx: typer.Context,
    question: str,
    answer: Annotated[str, typer.Option()],
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    output(
        lambda: update(
            ctx.obj,
            item_path(project, "questions", question),
            {"answer": answer},
            expected_revision,
            author,
            operation="answer",
        ),
        json_output,
    )


@inbox_app.command("follow-up")
def inbox_follow_up(
    ctx: typer.Context,
    identity: str,
    question: Annotated[str, typer.Option()],
    context: Annotated[str, typer.Option()],
    recommendation: Annotated[str, typer.Option()],
    choice: Annotated[list[str] | None, typer.Option("--choice")] = None,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    output(
        lambda: update(
            ctx.obj,
            item_path(project, "questions", identity),
            {
                "question": question,
                "context": context,
                "recommendation": recommendation,
                "choices": choice or [],
            },
            expected_revision,
            author,
            operation="follow-up",
        ),
        json_output,
    )


@inbox_app.command("apply")
def inbox_apply(
    ctx: typer.Context,
    question: str,
    decision: Annotated[str, typer.Option()],
    body: str | None = None,
    body_file: Path | None = None,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    expected_task_revision: int | None = None,
    expected_project_revision: int | None = None,
    description: str | None = None,
    description_file: Path | None = None,
    task_updates_file: Path | None = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Apply saved input atomically. Replacement text must be complete. No worker starts."""

    def run() -> Any:
        path = item_path(project, "questions", question)
        current = ctx.obj.request("GET", "context/" + path)
        data: dict[str, Any] = {"decision": decision}
        task_body = text_value(body, body_file)
        project_description = text_value(description, description_file)
        if task_body is not None:
            data["body"] = task_body
        if project_description is not None:
            data["description"] = project_description
        if expected_task_revision is not None:
            data["expected_task_revision"] = expected_task_revision
        if expected_project_revision is not None:
            data["expected_project_revision"] = expected_project_revision
        if task_updates_file is not None:
            try:
                data["task_updates"] = json.loads(task_updates_file.read_text())
            except json.JSONDecodeError as error:
                raise ApplicationError(
                    "invalid_request", f"Task updates must be valid JSON: {error.msg}."
                ) from error
        if current["task_id"]:
            if expected_task_revision is None:
                task = ctx.obj.request(
                    "GET", "context/" + item_path(project, "tasks", current["task_id"])
                )
                data["expected_task_revision"] = task["revision"]
        else:
            if expected_project_revision is None:
                record = ctx.obj.request("GET", project_path(project))
                data["expected_project_revision"] = record["revision"]
            if task_updates_file is None:
                data["task_updates"] = [
                    {
                        "task_id": identity,
                        "expected_revision": ctx.obj.request(
                            "GET", "context/" + item_path(project, "tasks", identity)
                        )["revision"],
                    }
                    for identity in current["affected_task_ids"]
                ]
        return update(
            ctx.obj,
            path,
            data,
            expected_revision if expected_revision is not None else current["revision"],
            author,
            operation="apply",
        )

    output(run, json_output)


@inbox_app.command("withdraw")
def inbox_withdraw(
    ctx: typer.Context,
    question: str,
    reason: Annotated[str, typer.Option()],
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    output(
        lambda: update(
            ctx.obj,
            item_path(project, "questions", question),
            {"reason": reason},
            expected_revision,
            author,
            operation="withdraw",
        ),
        json_output,
    )


@inbox_app.command("retract-answer")
def inbox_retract_answer(
    ctx: typer.Context,
    question: str,
    project: ProjectOption = None,
    expected_revision: Expected = None,
    author: Author = "human",
    json_output: Json = False,
) -> None:
    """Retract a saved, unapplied answer and return its question to Open."""
    output(
        lambda: update(
            ctx.obj,
            item_path(project, "questions", question),
            {},
            expected_revision,
            author,
            operation="retract-answer",
        ),
        json_output,
    )


from flowfield.execution_cli import register as register_execution  # noqa: E402
from flowfield.integration_cli import register as register_integration  # noqa: E402
from flowfield.storage_cli import register as register_storage  # noqa: E402
from flowfield.update_cli import register as register_updates  # noqa: E402

register_execution(project_app, task_app)
register_integration(project_app)
register_storage(app)
register_updates(app)
