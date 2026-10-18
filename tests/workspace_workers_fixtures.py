"""Reusable isolated setup and scripted peers; no collected test cases."""

from project_fixtures import existing_directory, task_request

from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskPublish, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit
from flowfield.workspaces import WorkspaceCreate, Workspaces


def shared(tmp_path, *, cap=1):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    for name in ("core", "site"):
        spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / name)))
        task = store.create_task(
            space.id,
            task_request(
                id=name,
                project_id=name,
                title="Work",
                body="Investigate and report findings.",
                status="up_next",
            ),
        )
        store.publish_task(
            space.id,
            task.id,
            TaskPublish(
                expected_revision=task.revision,
                completion="report",
            ),
        )
    saved = spaces.configure_workers(
        space.id,
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="shared"), max_parallel=cap),
    )
    spaces.queue(space.id, QueueEdit(expected_revision=saved.revision, enabled=True))
    return store, spaces, space.id, Execution(store)
