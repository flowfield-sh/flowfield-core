"""Local integration commands, grouped under the project that owns the target."""

from typing import Annotated, Any

import typer


def register(project_app: typer.Typer) -> None:
    from flowfield.cli import Json, ProjectOption, output, project_path

    app = typer.Typer(no_args_is_help=True, help="Configure delivery and inspect its checks.")
    project_app.add_typer(app, name="integration")

    def show(value: Any) -> None:
        if "items" in value:
            if not value["items"]:
                typer.echo("No integrations yet.")
            for item in value["items"]:
                typer.echo(f"{item['id']} · {item['task_key']} · {item['status']}")
            if value.get("next_before"):
                typer.echo(f"More: --before {value['next_before']}")
        elif "status" not in value:
            typer.echo(
                f"Target: {value['target_branch'] or 'not configured'} "
                f"· revision {value['revision']}"
            )
            for command in value["checks"]:
                typer.echo(f"Check: {command}")
        else:
            typer.echo(
                f"{value['task_key']} · {value['status']} · {value['id']} "
                f"· revision {value['revision']}"
            )
            typer.echo(
                f"Target: {value['target_branch']}\nBefore: {value['target_before']}\n"
                f"Candidate: {value['candidate_commit'] or 'none'}"
            )
            if value.get("problem"):
                typer.echo(value["problem"])
            for check in value["checks"]:
                typer.echo(f"\n{check['command']} · exit {check['exit_code']}\n{check['output']}")
            if value.get("workspace"):
                typer.echo(f"Checkout: {value['workspace']}")
            if value["status"] == "ready":
                typer.echo("Next: review the proposed result. Approval requests service delivery.")

    @app.command("configure")
    def configure(
        ctx: typer.Context,
        check: Annotated[
            list[str], typer.Option(help="Trusted local shell check; repeat for multiple checks.")
        ],
        target: str = typer.Option(
            ..., help="Destination branch; created from current HEAD when missing."
        ),
        setup: Annotated[list[str] | None, typer.Option(help="Trusted setup commands.")] = None,
        setup_timeout: int = typer.Option(120, min=1, max=900),
        check_timeout: int = typer.Option(60, min=1, max=900),
        local: bool = typer.Option(
            False,
            help="Use Local (the default environment).",
            hidden=True,
        ),
        create_from: str | None = typer.Option(
            None, help="Explicitly create the target from this commit/branch."
        ),
        project: ProjectOption = None,
        json_output: Json = False,
    ) -> None:
        def perform() -> Any:
            path = project_path(project) + "/integration"
            current = ctx.obj.request("GET", path)
            ctx.obj.request(
                "PUT",
                path,
                {
                    "expected_revision": current["revision"],
                    "target_branch": target,
                    "runtime": "local" if local else None,
                    "create_from": create_from,
                    "checks": check,
                    "setup_commands": setup or [],
                    "setup_timeout_seconds": setup_timeout,
                    "check_timeout_seconds": check_timeout,
                },
            )

            return ctx.obj.request("GET", path)

        output(perform, json_output, show)

    @app.command("show")
    def detail(
        ctx: typer.Context,
        integration_id: str | None = None,
        project: ProjectOption = None,
        json_output: Json = False,
    ) -> None:
        output(
            lambda: ctx.obj.request(
                "GET",
                project_path(project)
                + ("/integrations/" + integration_id if integration_id else "/integration"),
            ),
            json_output,
            show,
        )

    @app.command("list")
    def listing(
        ctx: typer.Context,
        run_id: str | None = None,
        before: int | None = None,
        project: ProjectOption = None,
        json_output: Json = False,
    ) -> None:
        from urllib.parse import urlencode

        query = {
            k: str(v) for k, v in {"run_id": run_id, "before": before}.items() if v is not None
        }
        output(
            lambda: ctx.obj.request(
                "GET", project_path(project) + "/integrations?" + urlencode(query)
            ),
            json_output,
            show,
        )
