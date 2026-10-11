"""Workspace-owned groupings, independent of repository execution and task dependencies."""

import json
import sqlite3
from typing import Any

from flowfield.application import (
    Milestone,
    MilestoneCreate,
    MilestoneEdit,
    Workspace,
    generated_id,
    now,
)
from flowfield.errors import ApplicationError
from flowfield.reads import page, validate_page
from flowfield.workspaces import Workspaces


class Milestones:
    def __init__(self, store: Workspace):
        self.store = store

    @staticmethod
    def read(db: sqlite3.Connection, workspace_id: str, identity: str) -> Milestone:
        row = db.execute(
            "SELECT data FROM milestones WHERE workspace_id=? "
            "AND (id=? OR key=? COLLATE NOCASE) ORDER BY id=? DESC",
            (workspace_id, identity, identity, identity),
        ).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Milestone not found in this workspace.", 404)
        return Milestone.model_validate_json(row[0])

    def get(self, workspace_id: str, identity: str) -> Milestone:
        with self.store.connection() as db:
            return self.read(db, workspace_id, identity)

    @staticmethod
    def read_all(db: sqlite3.Connection, workspace_id: str) -> list[Milestone]:
        Workspaces.read(db, workspace_id)
        return [
            Milestone.model_validate_json(row[0])
            for row in db.execute(
                "SELECT data FROM milestones WHERE workspace_id=? ORDER BY number", (workspace_id,)
            )
        ]

    def all(self, workspace_id: str) -> list[Milestone]:
        with self.store.connection() as db:
            return self.read_all(db, workspace_id)

    def page(
        self, workspace_id: str, *, after: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        validate_page(limit, after)
        with self.store.connection() as db:
            Workspaces.read(db, workspace_id)
            rows = db.execute(
                "SELECT json_remove(data,'$.body') FROM milestones WHERE workspace_id=? "
                "AND id>? ORDER BY id LIMIT ?",
                (workspace_id, after or "", limit + 1),
            ).fetchall()
            return page([json.loads(row[0]) for row in rows], limit, "id")

    def create(self, workspace_id: str, request: MilestoneCreate) -> Milestone:
        with self.store.connection(write=True) as db:
            Workspaces.read(db, workspace_id)
            number = db.execute(
                "SELECT coalesce(max(number),0)+1 FROM milestones WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
            value = Milestone(
                workspace_id=workspace_id,
                id=request.id or generated_id(request.title),
                key=f"M-{number}",
                title=request.title,
                body=request.body,
                revision=1,
                created_at=now(),
                updated_at=now(),
                updated_by=request.author,
            )
            try:
                db.execute(
                    "INSERT INTO milestones VALUES (?,?,?,?,?)",
                    (workspace_id, value.id, value.model_dump_json(), number, value.key),
                )
            except sqlite3.IntegrityError as error:
                raise ApplicationError(
                    "milestone_exists", "Milestone ID already exists.", 409
                ) from error
            return value

    def edit(self, workspace_id: str, identity: str, request: MilestoneEdit) -> Milestone:
        with self.store.connection(write=True) as db:
            value = Milestone(**self.store._patch(self.read(db, workspace_id, identity), request))
            db.execute(
                "UPDATE milestones SET data=? WHERE workspace_id=? AND id=?",
                (value.model_dump_json(), workspace_id, value.id),
            )
            return value

    def text(
        self,
        workspace_id: str,
        identity: str,
        *,
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 4000:
            raise ApplicationError(
                "invalid_request", "Offset must be nonnegative; limit must be 1–4000 characters."
            )
        value = self.get(workspace_id, identity)
        if revision is not None and revision != value.revision:
            raise ApplicationError("revision_conflict", "Text changed. Start reading again.", 409)
        text = value.body[offset : offset + limit]
        return {
            "id": value.id,
            "revision": value.revision,
            "field": "body",
            "text": text,
            "offset": offset,
            "total_characters": len(value.body),
            "next_offset": offset + len(text) if offset + len(text) < len(value.body) else None,
        }
