"""Document applicability edits share project defaults and preserve prepared sources."""

import sqlite3
from typing import Literal

from pydantic import Field

from flowfield.application import Input, ProjectEdit, Workspace
from flowfield.document_models import DocumentReference
from flowfield.documents import Documents
from flowfield.errors import ApplicationError
from flowfield.project_config import Identifier, Title
from flowfield.worker_documents import require_active

Purpose = Literal["required", "reference"]


class ProjectDocumentEdit(Input):
    id: Identifier
    expected_revision: int = Field(ge=1)
    purpose: Purpose | None


class ProjectDocumentUsage(Input):
    id: str
    name: str
    revision: int
    purpose: Purpose | None


class DocumentUsageView(Input):
    revision: int
    projectless: Purpose | None
    projects: list[ProjectDocumentUsage]


class DocumentUsageEdit(Input):
    expected_revision: int = Field(ge=1)
    author: Title = "human"
    projectless: Purpose | None
    projects: list[ProjectDocumentEdit] = Field(default_factory=list, max_length=1000)


class DocumentUsage:
    def __init__(self, store: Workspace):
        self.store = store

    @staticmethod
    def projectless_defaults(db: sqlite3.Connection, workspace_id: str) -> list[DocumentReference]:
        return [DocumentReference(id="guidance")] + [
            DocumentReference(id=row["id"], purpose=row["projectless"])
            for row in db.execute(
                "SELECT id,projectless FROM document_usage WHERE workspace_id=? "
                "AND projectless IS NOT NULL ORDER BY id",
                (workspace_id,),
            )
        ]

    def _read(self, db: sqlite3.Connection, workspace_id: str, identity: str) -> DocumentUsageView:
        Documents.read(db, workspace_id, identity)
        row = db.execute(
            "SELECT revision,projectless FROM document_usage WHERE workspace_id=? AND id=?",
            (workspace_id, identity),
        ).fetchone()
        projects = []
        for member in db.execute(
            "SELECT project_id FROM workspace_projects WHERE workspace_id=? ORDER BY project_id",
            (workspace_id,),
        ):
            project = self.store._project(db, member["project_id"])
            selected = next((ref for ref in project.worker_documents if ref.id == identity), None)
            projects.append(
                ProjectDocumentUsage(
                    id=project.id,
                    name=project.name,
                    revision=project.revision,
                    purpose=selected.purpose if selected else None,
                )
            )
        return DocumentUsageView(
            revision=row["revision"] if row else 1,
            projectless="required"
            if identity == "guidance"
            else row["projectless"]
            if row
            else None,
            projects=projects,
        )

    def get(self, workspace_id: str, identity: str) -> DocumentUsageView:
        with self.store.connection() as db:
            return self._read(db, workspace_id, identity)

    def edit(
        self, workspace_id: str, identity: str, request: DocumentUsageEdit
    ) -> DocumentUsageView:
        with self.store.connection(write=True, workspace_id=workspace_id) as db:
            require_active(Documents.read(db, workspace_id, identity))
            current = self._read(db, workspace_id, identity)
            self.store._current(current.revision, request.expected_revision)
            if identity == "guidance":
                raise ApplicationError(
                    "main_guidance", "Workspace guidance applies to all tasks.", 409
                )
            if len({p.id for p in request.projects}) != len(request.projects):
                raise ApplicationError("invalid_request", "Choose each project once.")
            for change in request.projects:
                if self.store._project_workspace(db, change.id) != workspace_id:
                    raise ApplicationError("not_found", "Project not found in this workspace.", 404)
                project = self.store._project(db, change.id)
                references = [ref for ref in project.worker_documents if ref.id != identity]
                selected = next(
                    (ref for ref in project.worker_documents if ref.id == identity), None
                )
                if change.purpose is not None:
                    references.append(
                        DocumentReference(
                            id=identity,
                            revision=selected.revision if selected else None,
                            purpose=change.purpose,
                        )
                    )
                self.store._edit_project(
                    db,
                    change.id,
                    ProjectEdit(
                        expected_revision=change.expected_revision,
                        worker_documents=references,
                        author=request.author,
                    ),
                )
            db.execute(
                "INSERT INTO document_usage VALUES (?,?,?,?) ON CONFLICT(workspace_id,id) "
                "DO UPDATE SET revision=excluded.revision,projectless=excluded.projectless",
                (workspace_id, identity, current.revision + 1, request.projectless),
            )
            if len(self.projectless_defaults(db, workspace_id)) > 21:
                raise ApplicationError(
                    "document_limit",
                    "Select at most 20 custom defaults for tasks without a project.",
                    409,
                )
            return self._read(db, workspace_id, identity)
