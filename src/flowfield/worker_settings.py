"""One workspace queue/default owner, projected through transitional project interfaces."""

import sqlite3

from flowfield.errors import ApplicationError
from flowfield.execution_models import (
    QueueEdit,
    SettingsEdit,
    WorkerOccupancy,
    WorkerSettings,
    WorkspaceWorkerSettings,
)


def workspace_id(db: sqlite3.Connection, project: str) -> str | None:
    row = db.execute(
        "SELECT workspace_id FROM workspace_scopes WHERE project_id=?", (project,)
    ).fetchone()
    return row[0] if row else None


def read_workspace(db: sqlite3.Connection, identity: str) -> WorkspaceWorkerSettings:
    if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (identity,)).fetchone():
        raise ApplicationError("not_found", "Workspace not found.", 404)
    row = db.execute(
        "SELECT data FROM workspace_worker_settings WHERE workspace_id=?", (identity,)
    ).fetchone()
    return (
        WorkspaceWorkerSettings.model_validate_json(row[0])
        if row
        else WorkspaceWorkerSettings(workspace_id=identity)
    )


def save_workspace(db: sqlite3.Connection, settings: WorkspaceWorkerSettings) -> None:
    settings.revision += 1
    db.execute(
        "INSERT INTO workspace_worker_settings VALUES (?,?) ON CONFLICT(workspace_id) "
        "DO UPDATE SET data=excluded.data",
        (settings.workspace_id, settings.model_dump_json()),
    )


def read_project(db: sqlite3.Connection, project: str) -> WorkerSettings:
    identity = workspace_id(db, project)
    if identity is not None:
        shared = read_workspace(db, identity)
        row = db.execute(
            "SELECT problem FROM project_worker_problems WHERE project_id=?", (project,)
        ).fetchone()
        return WorkerSettings(
            project_id=project,
            **shared.model_dump(exclude={"workspace_id"}),
            problem=row[0] if row else None,
        )
    row = db.execute("SELECT data FROM worker_settings WHERE project_id=?", (project,)).fetchone()
    return WorkerSettings.model_validate_json(row[0]) if row else WorkerSettings(project_id=project)


def save_problem(db: sqlite3.Connection, project: str, problem: str | None) -> None:
    if problem is None:
        db.execute("DELETE FROM project_worker_problems WHERE project_id=?", (project,))
    else:
        db.execute(
            "INSERT INTO project_worker_problems VALUES (?,?) ON CONFLICT(project_id) "
            "DO UPDATE SET problem=excluded.problem",
            (project, problem),
        )


def save_project(db: sqlite3.Connection, settings: WorkerSettings) -> None:
    identity = workspace_id(db, settings.project_id)
    if identity is not None:
        shared = WorkspaceWorkerSettings(
            workspace_id=identity, **settings.model_dump(exclude={"project_id", "problem"})
        )
        save_workspace(db, shared)
        settings.revision = shared.revision
        save_problem(db, settings.project_id, settings.problem)
        return
    settings.revision += 1
    db.execute(
        "INSERT INTO worker_settings VALUES (?,?) ON CONFLICT(project_id) "
        "DO UPDATE SET data=excluded.data",
        (settings.project_id, settings.model_dump_json()),
    )


def configure(settings: WorkerSettings | WorkspaceWorkerSettings, request: SettingsEdit) -> None:
    if request.selection.fast:
        raise ApplicationError(
            "worker_speed_override",
            "Set worker Fast mode on the individual task; workspace workers use normal speed.",
            409,
        )
    settings.selection = request.selection.model_copy(update={"fast": False}, deep=True)
    settings.max_parallel = request.max_parallel


def queue(settings: WorkerSettings | WorkspaceWorkerSettings, request: QueueEdit) -> None:
    if request.enabled and not settings.selection:
        raise ApplicationError(
            "worker_model_required",
            "Choose a worker harness and model, or ask the coordinator to configure them.",
            409,
        )
    settings.enabled = request.enabled


def occupancy(
    db: sqlite3.Connection, *, project: str | None = None, workspace: str | None = None
) -> WorkerOccupancy:
    if project is not None:
        workspace = workspace_id(db, project)
    if workspace is not None:
        rows = db.execute(
            "SELECT r.status,count(*) FROM runs r JOIN workspace_scopes m "
            "ON m.project_id=r.project_id WHERE m.workspace_id=? "
            "AND r.status IN ('preparing','running','stopping','uncertain') GROUP BY r.status",
            (workspace,),
        )
    else:
        rows = db.execute(
            "SELECT status,count(*) FROM runs WHERE project_id=? "
            "AND status IN ('preparing','running','stopping','uncertain') GROUP BY status",
            (project,),
        )
    counts = dict(rows.fetchall())
    reviews = db.execute(
        "SELECT r.status,count(*) FROM task_reviews r JOIN workspace_scopes m "
        "ON m.project_id=r.project_id WHERE (? IS NOT NULL AND m.workspace_id=? "
        "OR ? IS NULL AND r.project_id=?) AND r.status IN ('preparing','running','uncertain') "
        "GROUP BY r.status",
        (workspace, workspace, workspace, project),
    )
    for status, count in reviews:
        counts[status] = counts.get(status, 0) + count
    reviews = db.execute(
        "SELECT r.status,count(*) FROM milestone_reviews r JOIN workspace_scopes m "
        "ON m.project_id=r.project_id WHERE (? IS NOT NULL AND m.workspace_id=? "
        "OR ? IS NULL AND r.project_id=?) AND r.status IN ('preparing','running','uncertain') "
        "GROUP BY r.status",
        (workspace, workspace, workspace, project),
    )
    for status, count in reviews:
        counts[status] = counts.get(status, 0) + count
    return WorkerOccupancy(
        active=sum(counts.get(status, 0) for status in ("preparing", "running", "stopping")),
        uncertain=counts.get("uncertain", 0),
    )
