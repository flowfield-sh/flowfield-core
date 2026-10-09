"""Managed execution commands under the existing project/task namespaces."""

from typing import Any

import typer


def register(project_app: typer.Typer, task_app: typer.Typer) -> None:
    # Import after cli defines its shared option/output conventions.
    from flowfield.cli import Json, ProjectOption, output, project_path

    workers = typer.Typer(no_args_is_help=True, help="Configure workers and run/pause the queue.")
    runs = typer.Typer(no_args_is_help=True, help="Inspect, stop and retry task attempts.")
    project_app.add_typer(workers, name="workers")
    task_app.add_typer(runs, name="runs")
    results = typer.Typer(no_args_is_help=True, help="Review proposed results and their delivery.")
    task_app.add_typer(results, name="results")

    def show_result(value: Any) -> None:
        items = value.get("items", [value])
        for item in items:
            typer.echo(
                f"{item['task_key']} · Result v{item['version']} · {item['status']} · {item['id']}"
            )
            typer.echo(
                f"Revision {item['revision']} · "
                f"candidate {item.get('candidate_commit') or 'pending'}"
            )
            if item.get("target_branch"):
                typer.echo(f"Delivery target: {item['target_branch']}")
            if item.get("problem"):
                typer.echo(item["problem"])
            if item.get("report"):
                typer.echo(item["report"]["summary"])

    @results.command("list")
    def result_list(
        ctx: typer.Context,
        task: str,
        before: int | None = None,
        project: ProjectOption = None,
        json_output: Json = False,
    ) -> None:
        from urllib.parse import urlencode

        query = "?" + urlencode({"before": before}) if before is not None else ""
        output(
            lambda: ctx.obj.request("GET", project_path(project) + f"/tasks/{task}/results{query}"),
            json_output,
            show_result,
        )

    @results.command("show")
    def result_show(
        ctx: typer.Context, result_id: str, project: ProjectOption = None, json_output: Json = False
    ) -> None:
        output(
            lambda: ctx.obj.request("GET", project_path(project) + f"/results/{result_id}"),
            json_output,
            show_result,
        )

    @results.command("review")
    def result_review(
        ctx: typer.Context,
        result_id: str,
        action: str,
        candidate: str = typer.Option(...),
        expected_revision: int = typer.Option(..., min=1),
        note: str = "",
        author: str = "human",
        project: ProjectOption = None,
        json_output: Json = False,
    ) -> None:
        """Approve the exact validated result and request delivery, or request_changes."""
        output(
            lambda: ctx.obj.request(
                "POST",
                project_path(project) + f"/results/{result_id}/review",
                {
                    "expected_revision": expected_revision,
                    "candidate_commit": candidate,
                    "action": action,
                    "note": note,
                    "author": author,
                },
            ),
            json_output,
            show_result,
        )

    def result_operation(action: str) -> None:
        def perform(
            ctx: typer.Context,
            result_id: str,
            expected_revision: int = typer.Option(..., min=1),
            note: str = "",
            project: ProjectOption = None,
            json_output: Json = False,
        ) -> None:
            output(
                lambda: ctx.obj.request(
                    "POST",
                    project_path(project) + f"/results/{result_id}/{action}",
                    {"expected_revision": expected_revision, "note": note, "author": "human"},
                    timeout=18060,
                ),
                json_output,
                show_result,
            )

        results.command(action)(perform)

    for action in ("prepare", "correct", "cancel", "revalidate", "retry-delivery"):
        result_operation(action)

    def show(value: Any) -> None:
        if isinstance(value, list):
            for model in value:
                typer.echo(f"{model['id']} · {', '.join(model['efforts'])}")
        elif "items" in value:
            for item in value["items"]:
                typer.echo(f"{item['task_key']} · {item['id']} · {item['status']}")
            if value.get("next_before"):
                typer.echo(f"More: --before {value['next_before']}")
        elif "enabled" in value:
            choice = value.get("selection") or {}
            typer.echo(
                f"Queue {'running' if value['enabled'] else 'paused'} · "
                f"{choice.get('harness') or 'harness not selected'} · "
                f"{choice.get('model') or 'model not selected'}"
                + (f" · {choice['effort']}" if choice.get("effort") else "")
                + f" · maximum {value['max_parallel']} worker(s)"
            )
            if value.get("problem"):
                typer.echo(value["problem"])
        else:
            typer.echo(f"{value['task_key']} · {value['status']} · {value['id']}")
            typer.echo(f"{value['model']} · {value['effort']} · revision {value['revision']}")
            if value.get("problem"):
                typer.echo(value["problem"])
            if value.get("result"):
                for name in ("summary", "checks", "limitations"):
                    if value["result"].get(name):
                        typer.echo(f"\n{name.capitalize()}:\n{value['result'][name]}")
            if value.get("result_commit"):
                typer.echo(f"\nResult: {value['result_commit']}\nBase: {value['base_commit']}")
            if value.get("status") == "accepted":
                typer.echo("Code available" if value["code_available"] else "Awaiting integration")
            if value.get("location"):
                for command in value["location"].values():
                    if command:
                        typer.echo(command)

    @workers.command("show")
    def settings(
        ctx: typer.Context, project: ProjectOption = None, json_output: Json = False
    ) -> None:
        output(
            lambda: ctx.obj.request("GET", project_path(project) + "/workers"), json_output, show
        )

    @workers.command("models")
    def models(
        ctx: typer.Context,
        harness: str = "codex",
        project: ProjectOption = None,
        refresh: bool = False,
        json_output: Json = False,
    ) -> None:
        """Discover native choices; starts a disposable session and can run startup hooks."""
        from urllib.parse import urlencode

        def discover() -> Any:
            project_id = project_path(project).split("/")[-1]
            return ctx.obj.request(
                "GET",
                "worker-models?"
                + urlencode(
                    {"harness": harness, "project_id": project_id, "refresh": str(refresh).lower()}
                ),
                timeout=180,
            )

        output(discover, json_output, show)

    @workers.command("configure")
    def configure(
        ctx: typer.Context,
        model: str = typer.Option(...),
        effort: str | None = None,
        harness: str = "codex",
        mode: str | None = typer.Option(
            None,
            help="Access mode from `workers models --json`; choose explicitly for a new harness.",
        ),
        max_parallel: int = typer.Option(1, min=1, max=16),
        project: ProjectOption = None,
        json_output: Json = False,
    ) -> None:
        def perform() -> Any:
            path = project_path(project) + "/workers"
            current = ctx.obj.request("GET", path)
            choice = current.get("selection") or {}
            ctx.obj.request(
                "PUT",
                path,
                {
                    "expected_revision": current["revision"],
                    "selection": {
                        "harness": harness,
                        "model": model,
                        "effort": effort,
                        "mode": mode
                        if mode is not None
                        else (choice.get("mode") if choice.get("harness") == harness else None),
                        "fast": False,
                    },
                    "max_parallel": max_parallel,
                },
            )
            return ctx.obj.request("GET", path)

        output(perform, json_output, show)

    def queue(ctx: typer.Context, project: str | None, enabled: bool, json_output: bool) -> None:
        def perform() -> Any:
            path = project_path(project)
            current = ctx.obj.request("GET", path + "/workers")
            ctx.obj.request(
                "POST",
                path + "/queue",
                {"expected_revision": current["revision"], "enabled": enabled},
            )
            return ctx.obj.request("GET", path + "/workers")

        output(perform, json_output, show)

    @workers.command("run")
    def run(ctx: typer.Context, project: ProjectOption = None, json_output: Json = False) -> None:
        """Enable eligible Up next reservations; already running work is unaffected."""
        queue(ctx, project, True, json_output)

    @workers.command("pause")
    def pause(ctx: typer.Context, project: ProjectOption = None, json_output: Json = False) -> None:
        """Pause new starts. Stop an individual attempt separately."""
        queue(ctx, project, False, json_output)

    @runs.command("list")
    def listing(
        ctx: typer.Context,
        task: str | None = None,
        before: int | None = None,
        limit: int = typer.Option(10, min=1, max=50),
        project: ProjectOption = None,
        json_output: Json = False,
    ) -> None:
        from urllib.parse import urlencode

        query = {"limit": str(limit)}
        if task:
            query["task_id"] = task
        if before:
            query["before"] = str(before)
        output(
            lambda: ctx.obj.request("GET", project_path(project) + "/runs?" + urlencode(query)),
            json_output,
            show,
        )

    @runs.command("show")
    def detail(
        ctx: typer.Context, run_id: str, project: ProjectOption = None, json_output: Json = False
    ) -> None:
        def perform() -> Any:
            path = project_path(project) + "/runs/" + run_id
            return {
                **ctx.obj.request("GET", path),
                "location": ctx.obj.request("GET", path + "/location"),
            }

        output(perform, json_output, show)

    def mutate(
        ctx: typer.Context, run_id: str, project: str | None, action: str, json_output: bool
    ) -> None:
        def perform() -> Any:
            path = project_path(project) + "/runs/" + run_id
            current = ctx.obj.request("GET", path)
            ctx.obj.request(
                "POST",
                path + "/" + action,
                {"expected_revision": current["revision"], "author": "human"},
            )
            return ctx.obj.request("GET", path)

        output(perform, json_output, show)

    @runs.command("stop")
    def stop(
        ctx: typer.Context, run_id: str, project: ProjectOption = None, json_output: Json = False
    ) -> None:
        mutate(ctx, run_id, project, "stop", json_output)

    @runs.command("retry")
    def retry(
        ctx: typer.Context, run_id: str, project: ProjectOption = None, json_output: Json = False
    ) -> None:
        mutate(ctx, run_id, project, "retry", json_output)
