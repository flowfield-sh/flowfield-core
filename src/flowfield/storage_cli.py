"""Offline storage diagnostics; never initializes state or starts a service."""

import sqlite3

import typer

from flowfield import storage
from flowfield.errors import ApplicationError


def register(app: typer.Typer) -> None:
    from flowfield.cli import Json, output

    commands = typer.Typer(no_args_is_help=True, help="Inspect local storage offline.")
    app.add_typer(commands, name="storage")

    @commands.command("status")
    def status(ctx: typer.Context, json_output: Json = False) -> None:
        """Show schema compatibility without initializing or changing a database."""

        def inspect() -> dict[str, object]:
            try:
                return storage.status(ctx.obj.directory)
            except sqlite3.Error as error:
                raise ApplicationError(
                    "invalid_database", f"Cannot read workspace database: {error}"
                ) from error

        def show(value: dict[str, object]) -> None:
            typer.echo(f"Database: {value['database']}")
            typer.echo(
                f"Schema: {value['schema_version'] or 'not initialized'}; "
                f"installed build: {value['supported_version']}"
            )
            if value["schema_version"] is not None and (
                value["schema_version"] != value["supported_version"]
            ):
                typer.echo(
                    "Incompatible schema; preserve this directory "
                    "and use a compatible Flowfield version."
                )

        output(inspect, json_output, show)
