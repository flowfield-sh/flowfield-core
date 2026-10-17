"""Deterministic file delivery fixtures; saved queues remain paused and invoke no models."""

from pathlib import Path
from unittest.mock import patch

from project_fixtures import task_request

from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import TaskPreparation, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import SettingsEdit, WorkerResult
from flowfield.output_models import OutputSelection
from flowfield.results import Results
from flowfield.work_scopes import workspace_scope
from flowfield.workspaces import WorkspaceCreate, Workspaces


def setup(directory: Path):
    store = Workspace(directory)
    spaces = Workspaces(store)
    owner = spaces.create(WorkspaceCreate(name="Research files"))
    scope = workspace_scope(owner.id)
    spaces.configure_workers(
        owner.id,
        SettingsEdit(
            expected_revision=1,
            selection=AgentChoice(model="fixture", effort="low"),
        ),
    )
    return store, owner, scope


def task(store, scope, **values):
    return store.create_task(
        scope,
        task_request(
            title=values.pop("title", "Prepare launch research"),
            body="Produce the agreed report and source data. Check both before delivery.",
            status="up_next",
            preparation=TaskPreparation(completion="report"),
            **values,
        ),
    )


def deliver(store, scope, task_id, files=None, report=None):
    execution = Execution(store)
    settings = execution.settings(scope).model_copy(update={"enabled": True})
    with patch.object(execution, "_settings", return_value=settings):
        run = execution.claim(scope, "", {}, task_id=task_id)
    assert run
    environment = LocalHost({}).prepare_files(
        store.directory, Workspaces(store).for_project(scope).id, run.id
    )
    execution.save_local(
        run.id,
        {
            "runtime_kind": "local",
            "runtime": str(environment.runtime),
            "root": str(environment.root),
            "checkout": str(environment.checkout),
            "common_git": None,
        },
    )
    execution.started(scope, run.id)
    for name, content in (files or {}).items():
        path = environment.runtime / "output" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    report = report or WorkerResult(
        summary="Research report and source data are ready.",
        checks="Read back the report and verified the source data.",
        files=[OutputSelection(path=name) for name in (files or {})],
    )
    execution.report_result(scope, run.id, report)
    execution.finish(scope, run.id, "in_review", result=report)
    Results(store).process(scope)
    return Results(store).page(scope, task_id).items[0]
