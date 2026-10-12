"""Optional workspace classifications with no execution or approval semantics."""

import sqlite3
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field, StringConstraints

from flowfield.application import Edit, Input, TaskRevision, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.project_config import Identifier
from flowfield.workspaces import Workspaces

LabelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
LabelColor = Literal[
    "green",
    "green-light",
    "yellow",
    "yellow-light",
    "orange",
    "orange-light",
    "red",
    "red-light",
    "pink",
    "pink-light",
    "violet",
    "violet-light",
    "blue",
    "blue-light",
    "cyan",
    "cyan-light",
    "teal",
    "teal-light",
    "slate",
    "slate-light",
]
LabelGuidance = Annotated[str, StringConstraints(max_length=1000)]
DEFAULTS = (
    ("feature", "Feature", "blue", "New or expanded product behavior."),
    ("bug", "Bug", "orange", "Correct behavior that does not match the intended outcome."),
    (
        "maintenance",
        "Maintenance",
        "slate",
        "Upkeep of existing code, dependencies or documentation.",
    ),
    ("investigation", "Investigation", "violet", "Answer a question or reduce uncertainty."),
)


class LabelFields(Input):
    name: LabelName
    color: LabelColor = "slate"
    guidance: LabelGuidance = ""


class LabelCreate(LabelFields):
    author: str = Field(default="human", min_length=1, max_length=200)


class LabelEdit(Edit):
    name: LabelName | None = None
    color: LabelColor | None = None
    guidance: LabelGuidance | None = None


class WorkspaceLabel(LabelFields):
    workspace_id: Identifier
    id: Identifier
    revision: int
    created_at: str
    updated_at: str
    updated_by: str
    deleted_at: str | None = None


class Labels:
    def __init__(self, store: Workspace):
        self.store = store

    @staticmethod
    def seed(db: sqlite3.Connection, workspace_id: str, author: str, timestamp: str) -> None:
        db.executemany(
            "INSERT INTO workspace_labels VALUES (?,?,?,?,?,1,?,?,?,NULL)",
            [(workspace_id, *label, timestamp, timestamp, author) for label in DEFAULTS],
        )

    @staticmethod
    def read(db: sqlite3.Connection, workspace_id: str, identity: str) -> WorkspaceLabel:
        row = db.execute(
            "SELECT * FROM workspace_labels WHERE workspace_id=? AND id=?", (workspace_id, identity)
        ).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Label not found in this workspace.", 404)
        return WorkspaceLabel(**dict(row))

    @staticmethod
    def read_all(db: sqlite3.Connection, workspace_id: str) -> list[WorkspaceLabel]:
        Workspaces.read(db, workspace_id)
        return [
            WorkspaceLabel(**dict(row))
            for row in db.execute(
                "SELECT * FROM workspace_labels WHERE workspace_id=? "
                "ORDER BY name COLLATE NOCASE,id",
                (workspace_id,),
            )
        ]

    def all(self, workspace_id: str) -> list[WorkspaceLabel]:
        with self.store.connection() as db:
            return self.read_all(db, workspace_id)

    def page(
        self, workspace_id: str, *, after: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        from flowfield.reads import page, validate_page

        validate_page(limit, after)
        with self.store.connection() as db:
            Workspaces.read(db, workspace_id)
            values = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM workspace_labels WHERE workspace_id=? AND deleted_at IS NULL "
                    "AND id>? ORDER BY id LIMIT ?",
                    (workspace_id, after or "", limit + 1),
                )
            ]
            return page(values, limit, "id")

    @staticmethod
    def validate(db: sqlite3.Connection, workspace_id: str, identities: list[str]) -> None:
        for identity in identities:
            if Labels.read(db, workspace_id, identity).deleted_at:
                raise ApplicationError("label_deleted", "Choose an existing workspace label.", 409)

    def create(self, workspace_id: str, request: LabelCreate) -> WorkspaceLabel:
        with self.store.connection(write=True) as db:
            Workspaces.read(db, workspace_id)
            if (
                db.execute(
                    "SELECT count(*) FROM workspace_labels "
                    "WHERE workspace_id=? AND deleted_at IS NULL",
                    (workspace_id,),
                ).fetchone()[0]
                >= 100
            ):
                raise ApplicationError("label_limit", "A workspace can have up to 100 labels.", 409)
            value = WorkspaceLabel(
                **request.model_dump(exclude={"author"}),
                workspace_id=workspace_id,
                id=uuid4().hex,
                revision=1,
                created_at=now(),
                updated_at=now(),
                updated_by=request.author,
            )
            try:
                db.execute(
                    "INSERT INTO workspace_labels VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                    (
                        workspace_id,
                        value.id,
                        value.name,
                        value.color,
                        value.guidance,
                        1,
                        value.created_at,
                        value.updated_at,
                        value.updated_by,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ApplicationError(
                    "label_exists", "A label with this name already exists.", 409
                ) from error
            return value

    def edit(self, workspace_id: str, identity: str, request: LabelEdit) -> WorkspaceLabel:
        with self.store.connection(write=True) as db:
            current = self.read(db, workspace_id, identity)
            if current.deleted_at:
                raise ApplicationError("label_deleted", "This label was deleted.", 409)
            updated = WorkspaceLabel(**self.store._patch(current, request))
            try:
                db.execute(
                    "UPDATE workspace_labels SET name=?,color=?,guidance=?,revision=?,updated_at=?,"
                    "updated_by=? WHERE workspace_id=? AND id=?",
                    (
                        updated.name,
                        updated.color,
                        updated.guidance,
                        updated.revision,
                        updated.updated_at,
                        updated.updated_by,
                        workspace_id,
                        identity,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ApplicationError(
                    "label_exists", "A label with this name already exists.", 409
                ) from error
            return updated

    def delete(self, workspace_id: str, identity: str, request: Edit) -> WorkspaceLabel:
        with self.store.connection(write=True) as db:
            current = self.read(db, workspace_id, identity)
            self.store._patch(current, request)
            if current.deleted_at:
                return current
            timestamp = now()
            # Record removals as ordinary metadata revisions, preserving agreements,
            # publications, approvals and historical classification references.
            for row in db.execute(
                "SELECT t.data FROM tasks t JOIN workspace_projects p ON p.project_id=t.project_id "
                "WHERE p.workspace_id=? AND (EXISTS (SELECT 1 FROM json_each(t.data,'$.labels') "
                "WHERE value=?) OR (json_type(t.data,'$.labels') IS NULL "
                "AND json_extract(t.data,'$.task_type')=?))",
                (workspace_id, identity, identity),
            ).fetchall():
                task = TaskRevision.model_validate_json(row[0])
                task.labels = [label for label in task.labels if label != identity]
                task.revision += 1
                task.updated_at = timestamp
                task.updated_by = request.author
                self.store._save_task(db, task)
            db.execute(
                "UPDATE workspace_labels SET deleted_at=?,updated_at=?,updated_by=?,"
                "revision=revision+1 "
                "WHERE workspace_id=? AND id=?",
                (timestamp, timestamp, request.author, workspace_id, identity),
            )
            return self.read(db, workspace_id, identity)
