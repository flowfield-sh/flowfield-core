"""Bounded completed outcomes, projected from retained results without read state."""

import json
from typing import Literal

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.output_models import OutputFile
from flowfield.outputs import Outputs


class DeliveredTask(Record):
    id: str
    task_id: str
    task_key: str
    title: str
    version: int
    delivered_at: str
    completion: Literal["code", "report"]
    summary: str
    target_branch: str | None
    files: list[OutputFile]
    file_count: int


class DeliveredPage(Record):
    items: list[DeliveredTask]
    next_cursor: str | None = None


def delivery_page(
    store: Workspace,
    workspace_id: str,
    project_id: str | None = None,
    before: str | None = None,
    limit: int = 20,
) -> DeliveredPage:
    if not 1 <= limit <= 50:
        raise ApplicationError("invalid_limit", "Choose 1–50 delivered tasks.")
    stamp, identity = "", ""
    if before:
        try:
            cursor = json.loads(before)
            if (
                not isinstance(cursor, list)
                or len(cursor) != 2
                or not all(isinstance(x, str) for x in cursor)
            ):
                raise ValueError
            stamp, identity = cursor
        except (ValueError, TypeError):
            raise ApplicationError("invalid_cursor", "Invalid delivery cursor.") from None
    with store.connection() as db:
        store._workspace(db, workspace_id)
        rows = db.execute(
            "WITH delivered AS (SELECT r.id,r.task_id,r.version,r.data,t.key,"
            "json_extract(t.data,'$.title') AS title,"
            "json_extract(r.data,'$.completed_at') AS delivered_at "
            "FROM result_versions r JOIN tasks t ON t.workspace_id=r.workspace_id "
            "AND t.id=r.task_id "
            "WHERE r.workspace_id=:workspace AND r.status='delivered' "
            "AND json_extract(t.data,'$.status')='done' AND json_extract(t.data,'$.archived')=0 "
            "AND (:project IS NULL OR t.project_id=:project) "
            "AND NOT EXISTS (SELECT 1 FROM result_versions newer "
            "WHERE newer.workspace_id=r.workspace_id "
            "AND newer.task_id=r.task_id AND newer.version>r.version)) "
            "SELECT * FROM delivered WHERE delivered_at IS NOT NULL "
            "AND (:stamp='' OR (delivered_at,id)<(:stamp,:identity)) "
            "ORDER BY delivered_at DESC,id DESC LIMIT :limit",
            {
                "workspace": workspace_id,
                "project": project_id,
                "stamp": stamp,
                "identity": identity,
                "limit": limit + 1,
            },
        ).fetchall()
        items = []
        for row in rows[:limit]:
            result = json.loads(row["data"])
            outputs = result.get("output_ids", [])
            items.append(
                DeliveredTask(
                    id=row["id"],
                    task_id=row["task_id"],
                    task_key=row["key"],
                    title=row["title"],
                    version=row["version"],
                    delivered_at=row["delivered_at"],
                    completion=result["completion"],
                    summary=result["report"]["summary"][:600],
                    target_branch=result.get("target_branch"),
                    files=Outputs.selected(db, workspace_id, outputs[:3]),
                    file_count=len(outputs),
                )
            )
        return DeliveredPage(
            items=items,
            next_cursor=json.dumps([items[-1].delivered_at, items[-1].id])
            if len(rows) > limit
            else None,
        )
