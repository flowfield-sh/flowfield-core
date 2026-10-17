"""Prepare independent test directories before adopting them through production operations."""

from pathlib import Path

from flowfield.application import Project, ProjectSetup, Workspace


def existing_directory(path: str | Path) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(path)


def adopt(workspace: Workspace, request: ProjectSetup) -> Project:
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
    else:
        space = spaces.create(WorkspaceCreate(name=defaults.name + " Workspace"))
        identity = project_id
        with workspace.connection(write=True) as db:
            db.execute("PRAGMA defer_foreign_keys=ON")
            db.execute("UPDATE workspaces SET id=? WHERE id=?", (identity, space.id))
            db.execute(
                "UPDATE work_scopes SET id=?,workspace_id=? WHERE workspace_id=?",
                ("workspace_" + identity, identity, space.id),
            )
            db.execute(
                "UPDATE workspace_labels SET workspace_id=? WHERE workspace_id=?",
                (identity, space.id),
            )
            for table in ("workspace_documents", "workspace_document_revisions"):
                db.execute(
                    f"UPDATE {table} SET workspace_id=? WHERE workspace_id=?",
                    (identity, space.id),
                )
    project = spaces.adopt(
        identity, request.model_copy(update={"coordinator": None, "worker": None})
    )
    if request.coordinator:
        spaces.configure_coordinator(
            identity,
            AgentSettingsEdit(
                expected_revision=spaces.coordinator(identity).revision,
                selection=request.coordinator,
            ),
        )
    if request.worker:
        spaces.configure_workers(
            identity,
            SettingsEdit(
                expected_revision=spaces.workers(identity).revision,
                selection=request.worker,
                max_parallel=request.max_parallel,
            ),
        )
    return project


def task_request(**values):
    """An independent task fixture with no implicit execution checklist."""
    from flowfield.application import TaskCreate

    return TaskCreate(**values)
