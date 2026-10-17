"""Execution ownership for repository projects and repository-free workspace tasks."""

import sqlite3
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from flowfield.document_models import DocumentReference
from flowfield.errors import ApplicationError

if TYPE_CHECKING:
    from flowfield.application import Workspace


class WorkScope(BaseModel):
    id: str
    project_id: str | None = None
    workspace_id: str | None = None
    name: str
    path: str
    task_prefix: str
    description: str = ""
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""
    updated_by: str = ""
    worker_documents: list[DocumentReference] = Field(
        default_factory=lambda: [DocumentReference(id="guidance")]
    )


def workspace_scope(identity: str) -> str:
    return "workspace_" + identity


def read(store: "Workspace", db: sqlite3.Connection, identity: str) -> WorkScope:

    row = db.execute("SELECT * FROM work_scopes WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise ApplicationError("not_found", "Work scope not found.", 404)
    if row["project_id"]:
        project = store._project(db, row["project_id"])
        return WorkScope(
            **project.model_dump(), project_id=project.id, workspace_id=row["workspace_id"]
        )
    from flowfield.workspaces import Workspaces

    owner = Workspaces.read(db, row["workspace_id"])
    return WorkScope(
        id=identity,
        workspace_id=owner.id,
        name=owner.name,
        description=owner.description,
        path=str(store.directory / "workspaces" / owner.id),
        task_prefix="W",
        revision=owner.revision,
        created_at=owner.created_at,
        updated_at=owner.updated_at,
        updated_by=owner.updated_by,
    )
