"""User-created coordination spaces within the service's existing application store."""

import sqlite3
from typing import Annotated, Any
from uuid import uuid4

from pydantic import StringConstraints

from flowfield import worker_settings
from flowfield.agent_models import AgentSettingsEdit, AgentSettingsView
from flowfield.application import (
    Edit,
    Input,
    Markdown,
    Project,
    ProjectPrefix,
    ProjectSetup,
    Workspace,
    now,
)
from flowfield.coordinator_settings import CoordinatorSettings
from flowfield.errors import ApplicationError
from flowfield.execution_models import (
    QueueEdit,
    SettingsEdit,
    WorkerOccupancy,
    WorkspaceWorkerSettings,
)
from flowfield.project_config import Identifier, Title

# A migrated 200-character project name also retains the complete " Workspace" suffix.
WorkspaceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
]


class WorkspaceCreate(Input):
    name: WorkspaceName
    author: Title = "human"


class ProjectAdoption(Input):
    id: Identifier | None = None
    path: str
    name: Title | None = None
    task_prefix: ProjectPrefix | None = None
    author: Title = "human"


class WorkspaceEdit(Edit):
    name: WorkspaceName | None = None
    description: Markdown | None = None


class WorkspaceRecord(Input):
    id: Identifier
    name: WorkspaceName
    description: str = ""
    revision: int = 1
    created_at: str
    updated_at: str
    updated_by: str


class Workspaces:
    def __init__(self, store: Workspace):
        self.store = store

    @staticmethod
    def read(db: sqlite3.Connection, identity: str) -> WorkspaceRecord:
        row = db.execute("SELECT * FROM workspaces WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Workspace not found.", 404)
        return WorkspaceRecord(**dict(row))

    def get(self, identity: str) -> WorkspaceRecord:
        with self.store.connection() as db:
            return self.read(db, identity)

    def all(self) -> list[WorkspaceRecord]:
        with self.store.connection() as db:
            return [
                WorkspaceRecord(**dict(row))
                for row in db.execute("SELECT * FROM workspaces ORDER BY created_at, id")
            ]

    def page(self, *, after: str | None = None, limit: int = 20) -> dict[str, Any]:
        from flowfield.reads import page, validate_page

        validate_page(limit, after)
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT id,name,revision FROM workspaces WHERE (? IS NULL OR id>?) "
                "ORDER BY id LIMIT ?",
                (after, after, limit + 1),
            ).fetchall()
            return page([dict(row) for row in rows], limit, "id")

    def create(self, request: WorkspaceCreate) -> WorkspaceRecord:
        timestamp = now()
        record = WorkspaceRecord(
            id=uuid4().hex,
            name=request.name,
            created_at=timestamp,
            updated_at=timestamp,
            updated_by=request.author,
        )
        with self.store.connection(write=True) as db:
            db.execute(
                "INSERT INTO workspaces VALUES (?,?,?,?,?,?,?)",
                (
                    record.id,
                    record.name,
                    record.description,
                    record.revision,
                    record.created_at,
                    record.updated_at,
                    record.updated_by,
                ),
            )
            if self.store.schema_version >= 61:
                from flowfield.labels import Labels

                Labels.seed(db, record.id, request.author, timestamp)
        return record

    def edit(self, identity: str, request: WorkspaceEdit) -> WorkspaceRecord:
        with self.store.connection(write=True) as db:
            current = self.read(db, identity)
            changes = self.store._patch(current, request)
            updated = current.model_copy(update=changes)
            db.execute(
                "UPDATE workspaces SET name=?,description=?,revision=?,updated_at=?,updated_by=? "
                "WHERE id=?",
                (
                    updated.name,
                    updated.description,
                    updated.revision,
                    updated.updated_at,
                    updated.updated_by,
                    identity,
                ),
            )
            return updated

    def projects(self, identity: str) -> list[Project]:
        with self.store.connection() as db:
            self.read(db, identity)
            return [
                Project(**dict(row))
                for row in db.execute(
                    "SELECT p.* FROM projects p JOIN workspace_projects m ON m.project_id=p.id "
                    "WHERE m.workspace_id=? ORDER BY p.id",
                    (identity,),
                )
            ]

    def for_project(self, project_id: str) -> WorkspaceRecord:
        with self.store.connection() as db:
            self.store._project(db, project_id)
            row = db.execute(
                "SELECT workspace_id FROM workspace_projects WHERE project_id=?", (project_id,)
            ).fetchone()
            if row is None:
                raise ApplicationError(
                    "workspace_required", "Add this project to an explicit workspace first.", 409
                )
            return self.read(db, row[0])

    def require_project(self, identity: str, project_id: str) -> None:
        """A coordinator's current view cannot grant access to another workspace."""
        with self.store.connection() as db:
            self.check_project(db, identity, project_id)

    @staticmethod
    def check_project(db: sqlite3.Connection, identity: str, project_id: str) -> None:
        Workspaces.read(db, identity)
        if not db.execute(
            "SELECT 1 FROM workspace_projects WHERE workspace_id=? AND project_id=?",
            (identity, project_id),
        ).fetchone():
            raise ApplicationError("scope_mismatch", "Choose a project in this workspace.", 403)

    def project_page(
        self, identity: str, *, after: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        from flowfield.reads import page, validate_page

        validate_page(limit, after)
        with self.store.connection() as db:
            self.read(db, identity)
            rows = db.execute(
                "SELECT p.id,p.name,p.path,p.task_prefix,p.revision FROM projects p "
                "JOIN workspace_projects m ON m.project_id=p.id WHERE m.workspace_id=? "
                "AND (? IS NULL OR p.id>?) ORDER BY p.id LIMIT ?",
                (identity, after, after, limit + 1),
            ).fetchall()
            return page([dict(row) for row in rows], limit, "id")

    def adopt(self, identity: str, request: ProjectSetup) -> Project:
        return self.store.setup_project(request, workspace_id=identity)

    def workers(self, identity: str) -> WorkspaceWorkerSettings:
        with self.store.connection() as db:
            return worker_settings.read_workspace(db, identity)

    def coordinator(self, identity: str) -> AgentSettingsView:
        return CoordinatorSettings(self.store).get(identity)

    def configure_coordinator(self, identity: str, request: AgentSettingsEdit) -> AgentSettingsView:
        return CoordinatorSettings(self.store).edit(identity, request)

    def configure_workers(self, identity: str, request: SettingsEdit) -> WorkspaceWorkerSettings:
        with self.store.connection(write=True) as db:
            settings = worker_settings.read_workspace(db, identity)
            self.store._current(settings.revision, request.expected_revision)
            worker_settings.configure(settings, request)
            worker_settings.save_workspace(db, settings)
            return settings

    def queue(self, identity: str, request: QueueEdit) -> WorkspaceWorkerSettings:
        with self.store.connection(write=True) as db:
            settings = worker_settings.read_workspace(db, identity)
            self.store._current(settings.revision, request.expected_revision)
            worker_settings.queue(settings, request)
            worker_settings.save_workspace(db, settings)
            return settings

    def occupancy(self, identity: str) -> WorkerOccupancy:
        with self.store.connection() as db:
            self.read(db, identity)
            return worker_settings.occupancy(db, workspace=identity)
