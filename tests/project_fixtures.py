"""Prepare independent test directories before adopting them through production operations."""

from pathlib import Path

from flowfield.application import Project, ProjectSetup, Workspace


def existing_directory(path: str | Path) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(path)


def adopt(workspace: Workspace, request: ProjectSetup) -> Project:
    existing_directory(request.path)
    if workspace.schema_version < 58:
        return workspace.setup_project(request)
    from flowfield.agent_models import AgentSettingsEdit
    from flowfield.execution_models import SettingsEdit
    from flowfield.workspaces import WorkspaceCreate, Workspaces

    # Latest-schema fixtures explicitly create their workspace. Keep readable IDs,
    # matching the supported one-project-to-one-workspace migration, while shared
    # workspace tests use independently generated IDs and multiple member projects.
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
            if workspace.schema_version >= 61:
                db.execute(
                    "UPDATE workspace_labels SET workspace_id=? WHERE workspace_id=?",
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
    """An independent task fixture; stage-specific journeys supply their real plan."""
    from flowfield.application import TaskCreate
    from flowfield.stage_models import Stage

    values.setdefault(
        "stages",
        [
            Stage(
                id="fixture",
                title="Fixture setup",
                outcome="Prepare the isolated fixture",
                status="completed",
            )
        ],
    )
    return TaskCreate(**values)


def fixture_stage_change(workspace, project_id, task_id):
    """Explicitly reconcile fixture stages when a test changes its task agreement."""
    from flowfield.stage_models import StageChange
    from flowfield.stages import Stages

    current = Stages(workspace).get(project_id, task_id)
    return StageChange(
        expected_revision=current.revision, stages=current.stages, reason=current.reason
    )


def reconcile_fixture_stages(workspace, project_id, task_id):
    from flowfield.stage_models import StageUpdate
    from flowfield.stages import Stages

    change = fixture_stage_change(workspace, project_id, task_id)
    task = workspace.task(project_id, task_id)
    return Stages(workspace).update(
        project_id,
        task_id,
        StageUpdate(**change.model_dump(), agreement_revision=task.agreement_revision),
    )


def prepare_legacy_chat(workspace):
    """Let current test writers seed old chat records; finish_legacy_chat freezes old shape."""
    with workspace.connection(write=True) as db:
        db.execute(
            "ALTER TABLE coordinator_conversations ADD COLUMN welcome TEXT NOT NULL DEFAULT ''"
        )


def finish_legacy_chat(workspace):
    """Remove fixture-only welcome fields before exercising a pre-53 migration."""
    with workspace.connection(write=True) as db:
        db.execute("ALTER TABLE coordinator_conversations DROP COLUMN welcome")
        if db.execute(
            "SELECT name FROM sqlite_master WHERE name='coordinator_handoffs'"
        ).fetchone():
            db.execute("UPDATE coordinator_handoffs SET data=json_remove(data,'$.welcome')")
