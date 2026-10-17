"""Workspace-owned groupings, independent of repository execution and task dependencies."""

import json
import sqlite3
from typing import TYPE_CHECKING, Any, Literal

from flowfield.application import (
    Milestone,
    MilestoneCreate,
    MilestoneEdit,
    Workspace,
    generated_id,
    now,
)
from flowfield.errors import ApplicationError
from flowfield.milestone_models import MilestoneUpdate
from flowfield.reads import page, validate_page
from flowfield.workspaces import Workspaces

if TYPE_CHECKING:
    from flowfield.application import TaskRevision


def milestone_update(
    db: sqlite3.Connection,
    task: "TaskRevision",
    *,
    current_milestones: dict[tuple[str, str], Milestone] | None = None,
) -> MilestoneUpdate | None:
    binding = task.publication.milestone if task.publication else None
    if not binding or task.archived or task.status == "done" or binding.id != task.milestone_id:
        return None
    current = (
        current_milestones.get((binding.workspace_id, binding.id))
        if current_milestones is not None
        else Milestones.read(db, binding.workspace_id, binding.id)
    )
    if current is None:
        raise ApplicationError("not_found", "Milestone not found in this workspace.", 404)
    if current.revision <= binding.revision:
        return None
    return MilestoneUpdate(
        workspace_id=current.workspace_id,
        id=current.id,
        key=current.key,
        title=current.title,
        prepared_revision=binding.revision,
        latest_revision=current.revision,
    )


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

    @staticmethod
    def revision(
        db: sqlite3.Connection, workspace_id: str, identity: str, revision: int
    ) -> Milestone:
        current = Milestones.read(db, workspace_id, identity)
        row = db.execute(
            "SELECT data FROM milestone_revisions WHERE workspace_id=? AND id=? AND revision=?",
            (workspace_id, current.id, revision),
        ).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Milestone revision not retained.", 404)
        return Milestone.model_validate_json(row[0])

    def get(self, workspace_id: str, identity: str, revision: int | None = None) -> Milestone:
        with self.store.connection() as db:
            return (
                self.revision(db, workspace_id, identity, revision)
                if revision is not None
                else self.read(db, workspace_id, identity)
            )

    def history(
        self, workspace_id: str, identity: str, *, before: int | None = None, limit: int = 20
    ) -> dict[str, Any]:
        validate_page(limit, before)
        with self.store.connection() as db:
            current = self.read(db, workspace_id, identity)
            rows = db.execute(
                "SELECT json_remove(data,'$.body','$.acceptance_criteria') "
                "FROM milestone_revisions "
                "WHERE workspace_id=? AND id=? AND revision<? ORDER BY revision DESC LIMIT ?",
                (workspace_id, current.id, before or current.revision + 1, limit + 1),
            )
            return page([json.loads(row[0]) for row in rows], limit, "revision")

    def activity(
        self, workspace_id: str, identity: str, *, before: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Page existing revisions and review rounds without copying their full bodies."""
        validate_page(limit, before)
        with self.store.connection() as db:
            current = self.read(db, workspace_id, identity)
            rows = db.execute(
                "WITH entries AS ("
                "SELECT 'revision:'||revision id, 'revision' kind, revision number, "
                "json_extract(data,'$.updated_at') timestamp, "
                "json_extract(data,'$.updated_by') actor, NULL status, NULL verification_id "
                "FROM milestone_revisions WHERE workspace_id=? AND id=? UNION ALL "
                "SELECT 'verification:'||id, 'review', number, "
                "json_extract(data,'$.created_at'), "
                "CASE WHEN json_extract(data,'$.purpose')='review' THEN 'reviewer' "
                "ELSE 'worker' END, json_extract(data,'$.status'), id "
                "FROM milestone_verifications WHERE workspace_id=? AND milestone_id=?) "
                "SELECT *, timestamp||'|'||id cursor FROM entries "
                "WHERE (? IS NULL OR timestamp||'|'||id<?) ORDER BY cursor DESC LIMIT ?",
                (workspace_id, current.id, workspace_id, current.id, before, before, limit + 1),
            )
            return page([dict(row) for row in rows], limit, "cursor")

    def impact(
        self, workspace_id: str, identity: str, *, after: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        validate_page(limit, after)
        with self.store.connection() as db:
            current = self.read(db, workspace_id, identity)
            rows = db.execute(
                "SELECT t.project_id,t.id AS task_id,t.key AS task_key,"
                "json_extract(t.data,'$.title') AS task_title,"
                "json_extract(t.data,'$.status') AS task_status,"
                "json_extract(t.data,'$.publication.milestone.revision') AS prepared_revision,"
                "t.project_id||'/'||t.id AS cursor FROM tasks t "
                "JOIN workspace_scopes w ON w.project_id=t.project_id "
                "WHERE w.workspace_id=? AND json_extract(t.data,'$.milestone_id')=? "
                "AND json_extract(t.data,'$.publication.milestone.id')=? "
                "AND json_extract(t.data,'$.publication.milestone.revision')<? "
                "AND json_extract(t.data,'$.status')!='done' "
                "AND coalesce(json_extract(t.data,'$.archived'),0)=0 "
                "AND t.project_id||'/'||t.id>? ORDER BY t.project_id,t.id LIMIT ?",
                (workspace_id, current.id, current.id, current.revision, after or "", limit + 1),
            )
            return page(
                [{**dict(row), "latest_revision": current.revision} for row in rows],
                limit,
                "cursor",
            )

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
                "SELECT json_remove(data,'$.body','$.acceptance_criteria') "
                "FROM milestones WHERE workspace_id=? "
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
                acceptance_criteria=request.acceptance_criteria,
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
            current = self.read(db, workspace_id, identity)
            value = Milestone(**self.store._patch(current, request))
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
        field: Literal["body", "acceptance_criteria"] = "body",
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 4000:
            raise ApplicationError(
                "invalid_request", "Offset must be nonnegative; limit must be 1–4000 characters."
            )
        value = self.get(workspace_id, identity, revision)
        body = getattr(value, field)
        text = body[offset : offset + limit]
        return {
            "id": value.id,
            "revision": value.revision,
            "field": field,
            "text": text,
            "offset": offset,
            "total_characters": len(body),
            "next_offset": offset + len(text) if offset + len(text) < len(body) else None,
        }
