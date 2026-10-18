"""Task-local contribution planning for command-line coordinators."""

from typing import Annotated, Any

import typer


def register(task_app: typer.Typer) -> None:
    from flowfield.cli import Author, Expected, Json, WorkspaceOption, output, workspace_item_path

    app = typer.Typer(no_args_is_help=True, help="Plan a task's local contributions.")
    task_app.add_typer(app, name="subtask")

    @app.command("list")
    def listing(
        ctx: typer.Context, task: str, workspace: WorkspaceOption, json_output: Json = False
    ) -> None:
        output(
            lambda: ctx.obj.request(
                "GET", workspace_item_path(workspace, "tasks", task, "subtasks")
            ),
            json_output,
        )

    @app.command("create")
    def create(
        ctx: typer.Context,
        task: str,
        workspace: WorkspaceOption,
        title: Annotated[str, typer.Option()],
        body: Annotated[str, typer.Option()],
        completion: Annotated[str, typer.Option(help="code or report")],
        depends_on: Annotated[list[str] | None, typer.Option("--depends-on")] = None,
        author: Author = "human",
        json_output: Json = False,
    ) -> None:
        output(
            lambda: ctx.obj.request(
                "POST",
                workspace_item_path(workspace, "tasks", task, "subtasks"),
                {
                    "title": title,
                    "body": body,
                    "completion": completion,
                    "dependencies": depends_on or [],
                    "author": author,
                },
            ),
            json_output,
        )

    @app.command("edit")
    def edit(
        ctx: typer.Context,
        task: str,
        subtask: str,
        workspace: WorkspaceOption,
        title: str | None = None,
        body: str | None = None,
        completion: str | None = None,
        depends_on: Annotated[list[str] | None, typer.Option("--depends-on")] = None,
        no_dependencies: bool = False,
        expected_revision: Expected = None,
        author: Author = "human",
        json_output: Json = False,
    ) -> None:
        if depends_on and no_dependencies:
            raise typer.BadParameter("Choose dependencies or --no-dependencies.")

        def perform() -> Any:
            path = workspace_item_path(workspace, "tasks", task, "subtasks", subtask)
            revision = (
                expected_revision
                if expected_revision is not None
                else ctx.obj.request("GET", path)["revision"]
            )
            changes = {
                key: value
                for key, value in {
                    "title": title,
                    "body": body,
                    "completion": completion,
                    "dependencies": [] if no_dependencies else depends_on,
                }.items()
                if value is not None
            }
            return ctx.obj.request(
                "PUT", path, {**changes, "expected_revision": revision, "author": author}
            )

        output(perform, json_output)
