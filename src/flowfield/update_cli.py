"""Explicit update status/check commands, using the service or a read-only offline cache."""

import sqlite3
import time
from typing import Any

import typer

from flowfield.client import Client
from flowfield.errors import ApplicationError
from flowfield.updates import UpdateStatus, cached_status


def read_status(client: Client) -> UpdateStatus:
    try:
        return UpdateStatus.model_validate(client.request("GET", "updates", timeout=2))
    except ApplicationError as error:
        if error.code != "service_unavailable":
            raise
        try:
            return cached_status(client.directory)
        except sqlite3.Error as failure:
            raise ApplicationError(
                "invalid_database", f"Cannot read update cache: {failure}"
            ) from failure


def register(app: typer.Typer) -> None:
    from flowfield.cli import Json, output

    commands = typer.Typer(no_args_is_help=True, help="Check for releases; never installs updates.")
    app.add_typer(commands, name="update")

    def human(value: dict[str, Any], client: Client) -> None:
        status = UpdateStatus.model_validate(value)
        typer.echo(f"Installed: {status.installed_version}")
        if status.cached:
            typer.echo("Service offline; showing saved update status.")
            typer.echo(f"Start it with: {client.serve_command()}")
            typer.echo("Keep that terminal open while using Flowfield.")
        if status.available_version:
            typer.echo(f"Flowfield {status.available_version} is available.", err=True)
            typer.echo(f"Release notes: {status.release_notes}", err=True)
            typer.echo("Stop Flowfield before upgrading; restart afterward.", err=True)
            for command in status.commands:
                typer.echo(f"{command.label}: {command.command}", err=True)
        elif status.last_success and not status.error:
            typer.echo("No newer compatible release found at the last successful check.")
        elif not status.last_success:
            typer.echo("No successful update check yet.")
        if status.last_success:
            typer.echo(f"Last successful check: {status.last_success}")
        if status.error:
            typer.echo(status.error, err=True)
        if status.checking:
            typer.echo("Checking for updates…")

    @commands.command("status")
    def status(ctx: typer.Context, json_output: Json = False) -> None:
        """Read shared status, or its saved cache when the service is stopped."""
        output(
            lambda: read_status(ctx.obj).model_dump(),
            json_output,
            lambda value: human(value, ctx.obj),
        )

    @commands.command("check")
    def check(ctx: typer.Context, json_output: Json = False) -> None:
        """Request a bounded manual check from the running service."""

        def run() -> dict[str, Any]:
            ctx.obj.request("POST", "updates/check", {"reason": "manual"}, timeout=2)
            deadline = time.monotonic() + 8
            while True:
                value = UpdateStatus.model_validate(ctx.obj.request("GET", "updates", timeout=2))
                if not value.checking or time.monotonic() >= deadline:
                    if value.error:
                        raise ApplicationError("update_check_failed", value.error)
                    return value.model_dump()
                time.sleep(0.1)

        output(run, json_output, lambda value: human(value, ctx.obj))
