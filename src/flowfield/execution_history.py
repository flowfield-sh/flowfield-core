"""Read-only typed execution history; human review decisions are not executions."""

import sqlite3
from typing import Literal

from flowfield.activity import ActivityEntry
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record


class ExecutionItem(Record):
    id: str
    kind: Literal["worker", "validation", "delivery"]
    purpose: str
    status: str
    owner: str
    created_at: str
    ended_at: str | None = None
    run_id: str
    integration_id: str | None = None
    result_id: str | None = None
    version: int | None = None


class ExecutionPage(Record):
    items: list[ExecutionItem]
    next_offset: int | None = None


class TaskHistoryPage(Record):
    items: list[ActivityEntry | ExecutionItem]
    next_offset: int | None = None


_PROJECTION = """
WITH selected AS (
    SELECT * FROM result_versions v WHERE workspace_id=:project AND task_id=:task
), history AS (
    SELECT 'worker:'||r.id AS id, 'worker' AS kind,
        CASE WHEN json_extract(r.data,'$.input_question_id') IS NOT NULL
             THEN 'Continue after answer'
             WHEN json_extract(r.data,'$.correction') IS NOT NULL THEN 'Correct result'
             WHEN json_extract(r.data,'$.predecessor_id') IS NULL THEN 'Implement task'
             ELSE 'Revise result' END AS purpose,
        CASE WHEN r.status IN ('in_review','accepted','changes_requested') THEN 'completed'
             ELSE r.status END AS status,
        json_extract(r.data,'$.model') AS owner,
        json_extract(r.data,'$.created_at') AS created_at,
        json_extract(r.data,'$.ended_at') AS ended_at,
        r.id AS run_id, NULL AS integration_id, v.id AS result_id, v.version
    FROM runs r LEFT JOIN selected v ON v.run_id=r.id AND NOT EXISTS (
        SELECT 1 FROM selected n WHERE n.run_id=r.id AND n.version>v.version)
    WHERE r.workspace_id=:project AND r.task_id=:task
    UNION ALL
    SELECT 'validation:'||i.id, 'validation',
        CASE WHEN json_extract(i.data,'$.purpose')='availability' THEN 'Revalidate target for v'
             ELSE 'Validate result v' END ||v.version,
        CASE WHEN i.status='preparing' THEN 'running'
             WHEN i.status IN ('ready','applying','integrated') THEN 'completed'
             WHEN i.status != 'failed' AND json_extract(i.data,'$.validated_at') IS NOT NULL
                  AND json_extract(i.data,'$.candidate_commit') IS NOT NULL
                  AND json_array_length(json_extract(i.data,'$.checks'))>0
                  AND NOT EXISTS (SELECT 1 FROM json_each(i.data,'$.checks') c
                                  WHERE json_extract(c.value,'$.exit_code')!=0)
             THEN 'completed' ELSE 'failed' END,
        'Service', json_extract(i.data,'$.created_at'),
        coalesce(json_extract(i.data,'$.validated_at'),json_extract(i.data,'$.completed_at')),
        i.run_id, i.id, v.id, v.version
    FROM integrations i JOIN selected v ON json_extract(v.data,'$.integration_id')=i.id
        OR json_extract(i.data,'$.result_id')=v.id
    UNION ALL
    SELECT 'delivery:'||v.id, 'delivery', 'Deliver result v'||v.version,
        CASE WHEN v.status='delivered' OR i.status='integrated' THEN 'completed'
             WHEN i.status='applying' THEN 'running'
             WHEN v.status='delivering' THEN 'queued' ELSE 'blocked' END,
        'Service', json_extract(v.data,'$.approved_at'),
        json_extract(v.data,'$.completed_at'), v.run_id,
        json_extract(v.data,'$.integration_id'), v.id, v.version
    FROM selected v LEFT JOIN integrations i ON json_extract(v.data,'$.integration_id')=i.id
    WHERE json_extract(v.data,'$.completion')='code'
        AND json_extract(v.data,'$.approved_at') IS NOT NULL
)
"""


class ExecutionHistory:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _args(self, db: sqlite3.Connection, workspace_id: str, task_id: str) -> dict[str, str]:
        identity = self.workspace._activity_scope(db, workspace_id, task_id)
        assert identity is not None
        return {"project": workspace_id, "task": identity}

    def page(
        self,
        workspace_id: str,
        task_id: str,
        offset: int = 0,
        limit: int = 20,
        run_id: str | None = None,
    ) -> ExecutionPage:
        with self.workspace.connection() as db:
            args = self._args(db, workspace_id, task_id)
            rows = db.execute(
                _PROJECTION + "SELECT * FROM history WHERE (:run IS NULL OR run_id=:run) "
                "ORDER BY created_at DESC,id "
                "LIMIT :limit OFFSET :offset",
                {**args, "limit": limit + 1, "offset": offset, "run": run_id},
            ).fetchall()
            return ExecutionPage(
                items=[ExecutionItem.model_validate(dict(row)) for row in rows[:limit]],
                next_offset=offset + limit if len(rows) > limit else None,
            )

    def timeline(
        self,
        workspace_id: str,
        task_id: str,
        offset: int = 0,
        limit: int = 30,
        mode: Literal["all", "notes", "attempts"] = "all",
    ) -> TaskHistoryPage:
        """Page one chronology before hydrating entries; checks nest under attempts."""
        with self.workspace.connection() as db:
            args = {
                **self._args(db, workspace_id, task_id),
                "mode": mode,
                "limit": limit + 1,
                "offset": offset,
            }
            rows = db.execute(
                _PROJECTION
                + """, timeline AS (
                    SELECT id, 'attempt' AS category, created_at, 0 AS sequence FROM history
                    WHERE kind='worker' AND :mode IN ('all','attempts')
                    UNION ALL
                    SELECT a.id, 'activity', a.created_at, a.sequence FROM activity a
                    WHERE a.workspace_id=:project AND a.task_id=:task AND (
                        :mode='all' OR (:mode='notes' AND a.kind IN ('note','handoff')))
                ) SELECT * FROM timeline ORDER BY created_at DESC,sequence DESC,id
                LIMIT :limit OFFSET :offset""",
                args,
            ).fetchall()
            items: list[ActivityEntry | ExecutionItem] = []
            for row in rows[:limit]:
                if row["category"] == "activity":
                    items.append(self.workspace._activity_entry(db, row["id"]))
                else:
                    execution = db.execute(
                        _PROJECTION + "SELECT * FROM history WHERE id=:id",
                        {**args, "id": row["id"]},
                    ).fetchone()
                    items.append(ExecutionItem.model_validate(dict(execution)))
            return TaskHistoryPage(
                items=items, next_offset=offset + limit if len(rows) > limit else None
            )

    def get(self, workspace_id: str, task_id: str, identity: str) -> ExecutionItem:
        with self.workspace.connection() as db:
            args = self._args(db, workspace_id, task_id)
            row = db.execute(
                _PROJECTION + "SELECT * FROM history WHERE id=:id", {**args, "id": identity}
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Execution not found for this task.", 404)
            return ExecutionItem.model_validate(dict(row))
