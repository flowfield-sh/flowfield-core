"""Informational source freshness, independent of task readiness and frozen attempts."""

import sqlite3

from flowfield.document_models import DocumentUpdate

# Project membership scopes document identity; only current unfinished preparations matter.
# This reads small headers and snapshot metadata, never document bodies or task history.
UPDATED_SOURCES = """
SELECT t.workspace_id,t.id AS task_id,t.key AS task_key,
    json_extract(t.data,'$.title') AS task_title,
    json_extract(t.data,'$.status') AS task_status,
    d.workspace_id,d.id,r.title,
    json_extract(s.value,'$.revision') AS prepared_revision,
    d.revision AS latest_revision,
    coalesce(json_extract(s.value,'$.purpose'),'required') AS purpose,
    r.created_at AS updated_at
FROM tasks t
JOIN json_each(t.data,'$.publication.documents') s
JOIN workspace_documents d ON d.workspace_id=t.workspace_id
    AND d.id=json_extract(s.value,'$.id')
JOIN workspace_document_revisions r ON r.workspace_id=d.workspace_id
    AND r.id=d.id AND r.revision=d.revision
WHERE json_extract(t.data,'$.status')!='done'
    AND coalesce(json_extract(t.data,'$.archived'),0)=0
    AND d.revision>json_extract(s.value,'$.revision')
"""


def task_updates(db: sqlite3.Connection, project: str, task: str) -> list[DocumentUpdate]:
    return project_updates(db, project, task).get(task, [])


def project_updates(
    db: sqlite3.Connection, project: str, task: str | None = None
) -> dict[str, list[DocumentUpdate]]:
    updates: dict[str, list[DocumentUpdate]] = {}
    for row in db.execute(
        "SELECT * FROM ("
        + UPDATED_SOURCES
        + ") WHERE workspace_id=? "
        + ("AND task_id=? " if task is not None else "")
        + "ORDER BY id",
        (project, task) if task is not None else (project,),
    ):
        updates.setdefault(row["task_id"], []).append(DocumentUpdate.model_validate(dict(row)))
    return updates
