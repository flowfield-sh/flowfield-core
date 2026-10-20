"""Workspace queue, capacity and worker defaults."""

import sqlite3

from flowfield.errors import ApplicationError
from flowfield.execution_models import (
    QueueEdit,
    SettingsEdit,
    WorkerOccupancy,
    WorkspaceWorkerSettings,
)


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


def configure(settings: WorkspaceWorkerSettings, request: SettingsEdit) -> None:
    settings.selection = request.selection.model_copy(
        update={"fast": request.selection.fast is True}, deep=True
    )
    settings.max_parallel = request.max_parallel


def queue(settings: WorkspaceWorkerSettings, request: QueueEdit) -> None:
    if request.enabled and not settings.selection:
        raise ApplicationError(
            "worker_model_required", "Choose a worker harness and model first.", 409
        )
    settings.enabled = request.enabled


def occupancy(db: sqlite3.Connection, *, workspace: str) -> WorkerOccupancy:
    counts: dict[str, int] = {}
    for table in ("runs", "task_reviews"):
        for status, count in db.execute(
            f"SELECT status,count(*) FROM {table} WHERE workspace_id=? "
            "AND status IN ('preparing','running','stopping','uncertain') GROUP BY status",
            (workspace,),
        ):
            counts[status] = counts.get(status, 0) + count
    return WorkerOccupancy(
        active=sum(counts.get(status, 0) for status in ("preparing", "running", "stopping")),
        uncertain=counts.get("uncertain", 0),
    )
