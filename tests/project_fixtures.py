"""Prepare independent test directories before adopting them through production operations."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flowfield.application import Project, ProjectSetup, Workspace


def existing_directory(path: str | Path) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(path)


def adopt(
    workspace: Workspace, request: ProjectSetup, *, coordinator=None, worker=None, max_parallel=1
) -> Project:
    existing_directory(request.path)
    from flowfield.agent_models import AgentSettingsEdit
    from flowfield.execution_models import SettingsEdit
    from flowfield.workspaces import WorkspaceCreate, Workspaces

    # Fixtures explicitly create workspaces with readable IDs. Shared-workspace
    # tests use independently generated IDs and multiple member projects.
    defaults = workspace.project_setup_defaults(request)
    project_id = request.id or defaults.id
    spaces = Workspaces(workspace)
    with workspace.connection() as db:
        membership = db.execute(
            "SELECT workspace_id FROM workspace_projects WHERE project_id=?", (project_id,)
        ).fetchone()
    if membership:
        identity = membership[0]
    elif any(space.id == project_id for space in spaces.all()):
        identity = project_id
    else:
        identity = project_id
        # Publish the final identity atomically: a running service can observe it
        # immediately, so never create then rename the workspace behind its back.
        with patch("flowfield.workspaces.uuid4", return_value=SimpleNamespace(hex=identity)):
            spaces.create(WorkspaceCreate(name=defaults.name + " Workspace"))
    project = spaces.adopt(identity, request)
    if coordinator:
        spaces.configure_coordinator(
            identity,
            AgentSettingsEdit(
                expected_revision=spaces.coordinator(identity).revision,
                selection=coordinator,
            ),
        )
    if worker:
        spaces.configure_workers(
            identity,
            SettingsEdit(
                expected_revision=spaces.workers(identity).revision,
                selection=worker,
                max_parallel=max_parallel,
            ),
        )
    return project


def task_request(**values):
    """An independent task fixture with no implicit execution checklist."""
    from flowfield.application import TaskCreate

    return TaskCreate(**values)
