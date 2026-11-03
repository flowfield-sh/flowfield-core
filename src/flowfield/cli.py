"""Service operation, external-agent integration and essential host diagnostics."""

import errno
import json
import os
import re
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import typer
import uvicorn
from rich.console import Console

from flowfield import __version__, cli_views
from flowfield.client import Client
from flowfield.errors import ApplicationError
from flowfield.project_config import discover_project
from flowfield.state import data_path

app = typer.Typer(no_args_is_help=True, help="Run Flowfield and connect external coding agents.")
integration_app = typer.Typer(no_args_is_help=True)
harness_app = typer.Typer(no_args_is_help=True)
app.add_typer(
    integration_app,
    name="integration",
    help="Connect external agents and manage optional project guidance.",
)
app.add_typer(
    harness_app, name="harness", help="Inspect native harness readiness and recover discovery."
)
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
    human: Callable[[Any], None],
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
    else:
        human(result)


def connection_command(
    ctx: typer.Context, harness: str, name: str, operation: str, json_output: bool
) -> None:
    from flowfield.adapters.connection_selection import connection

    def run() -> dict[str, Any]:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) is None:
            raise ApplicationError(
                "invalid_connection_name",
                "Connection names use 1–64 lowercase letters, numbers, hyphens or underscores.",
            )
        client = connection(harness, ctx.obj.port, name)
        return {
            "connect": client.connect,
            "doctor": client.doctor,
            "disconnect": client.disconnect,
        }[operation]()

    def display(result: dict[str, Any]) -> None:
        typer.echo(result["message"])
        if "url" in result:
            typer.echo(
                f"MCP server: {name}\nEndpoint: {result['url']}\n"
                f"MCP tools verified: {len(result['tools'])}"
            )
        if operation == "connect":
            typer.echo("In your harness, ask: Use Flowfield to list my projects.")

    output(run, json_output, display)


@harness_app.command("status")
def harness_status(ctx: typer.Context, harness: str, json_output: Json = False) -> None:
    """Inspect detected native paths offline without starting an agent."""
    from flowfield.adapters.harness_host import status
    from flowfield.harness_settings import KINDS, offline_registration

    def run() -> dict[str, Any]:
        if harness not in KINDS:
            raise ApplicationError("unsupported_harness", "This harness is not supported.")
        return status(
            ctx.obj.directory, offline_registration(ctx.obj.directory, harness), os.environ
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


@integration_app.command()
def connect(
    ctx: typer.Context,
    harness: str,
    name: Annotated[str, typer.Option(help="Native MCP connection name.")] = "flowfield",
    json_output: Json = False,
) -> None:
    """Connect a standalone harness to the running service; preserve existing harness settings."""
    connection_command(ctx, harness, name, "connect", json_output)


@integration_app.command("status")
def doctor(
    ctx: typer.Context,
    harness: str,
    name: Annotated[str, typer.Option(help="Native MCP connection name.")] = "flowfield",
    json_output: Json = False,
) -> None:
    """Check harness configuration and discover service tools without a model call."""
    connection_command(ctx, harness, name, "doctor", json_output)


@integration_app.command()
def disconnect(
    ctx: typer.Context,
    harness: str,
    name: Annotated[str, typer.Option(help="Native MCP connection name.")] = "flowfield",
    json_output: Json = False,
) -> None:
    """Remove this service's standalone connection; retain projects and tasks."""
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
    selected_port = port or client.port
    try:
        listener = socket.create_server(("127.0.0.1", selected_port))
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        typer.echo(
            f"Cannot start Flowfield: port {selected_port} is already in use.\n"
            f"If Flowfield is already running, open http://127.0.0.1:{selected_port}.\n"
            "Otherwise stop the application using this port or choose another with "
            "'flowfield serve --port PORT'.",
            err=True,
        )
        raise typer.Exit(1) from None
    # Reserve the port before workspace initialization, and retain ownership through shutdown.
    with listener:
        BrowserServer(
            uvicorn.Config(
                create_app(data_dir=client.directory), host="127.0.0.1", port=selected_port
            ),
            open_browser=open_browser,
        ).run(sockets=[listener])


@integration_app.command("guidance")
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


def project_path(project: str | None) -> str:
    identity = project if project is not None else discover_project(Path.cwd())
    return f"projects/{quote(identity, safe='')}"


from flowfield.storage_cli import register as register_storage  # noqa: E402
from flowfield.update_cli import register as register_updates  # noqa: E402

register_storage(app)
register_updates(app)
