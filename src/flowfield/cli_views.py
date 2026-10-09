"""Human CLI views for host diagnostics, task stages and source-linked search."""

import shlex
from typing import Any

import typer


def harness_status(value: dict[str, Any], port: int) -> None:
    registration, launch = value["registration"], value["launch"]
    name = {"codex": "Codex", "claude-code": "Claude Code", "pi": "Pi"}[registration["harness"]]
    typer.echo(f"{name} · settings revision {registration['revision']}")
    typer.echo(f"Executable: {launch['native_executable'] or 'Not found'}")
    typer.echo(f"Configuration directory: {launch['config_directory']}")
    if value.get("native_version"):
        typer.echo(f"Native version: {value['native_version']}")
    authentication = {
        "unknown": "Not checked",
        "authenticated": "Signed in",
        "signed-out": "Signed out",
    }
    typer.echo(f"Sign-in: {authentication[value['authentication']]}")
    typer.echo("Model access: Not verified")
    problems = {
        "native_missing": "Install the native harness or correct its executable path.",
        "config_directory_missing": "Choose an existing native configuration directory.",
        "authentication_unverified": "Sign-in could not be verified; check the native harness.",
        "native_login_required": "Sign in through the native harness.",
        "native_check_failed": "The native setup check failed; check the executable and sign-in.",
        "catalog_running": "Model or command discovery is still running.",
        "catalog_uncertain": "Discovery stopped unexpectedly; inspect remaining native work.",
    }
    for problem in value["problems"]:
        typer.echo(f"Next: {problems.get(problem, problem.replace('_', ' '))}")
    if ownership := value.get("catalog_ownership"):
        typer.echo(f"Discovery: {ownership['id']} · {ownership['status']}")
        if ownership["status"] == "uncertain":
            typer.echo("After stopping remaining native discovery on the service host, run:")
            typer.echo(
                "  "
                + shlex.join(
                    [
                        "flowfield",
                        "--port",
                        str(port),
                        "harness",
                        "confirm-stopped",
                        registration["harness"],
                        "--discovery",
                        ownership["id"],
                    ]
                )
            )


def stages(value: dict[str, Any]) -> None:
    typer.echo(
        f"Stages for {value['task_id']} · revision {value['revision']} "
        f"· agreement {value['agreement_revision']}"
    )
    if not value["stages"]:
        typer.echo("No stages recorded.")
    for index, stage in enumerate(value["stages"], 1):
        typer.echo(f"\n{index}. {stage['title']} · {stage['status'].capitalize()}")
        typer.echo(f"   {stage['outcome']}")
        typer.echo(f"   ID: {stage['id']}")
    if value.get("reason"):
        typer.echo(f"\nLatest update: {value['reason']}")


def search(value: dict[str, Any]) -> None:
    if not value["items"]:
        typer.echo("No matching evidence.")
    for item in value["items"]:
        state = "Current" if item["current"] else "Historical"
        typer.echo(
            f"{item['title'] or item['identity']} · {state} "
            f"· {item['entity']} {item['identity']} · revision {item['revision']}"
        )
        typer.echo(item["snippet"])
        typer.echo(f"Open: {item['url']}\n")
    if value.get("next_cursor") is not None:
        typer.echo(f"More available: --before {value['next_cursor']}")
