"""Bounded, derived human attention; no second workflow or mutable inbox records."""

import sqlite3
from collections.abc import Iterator
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from flowfield.work_state import WorkState, question_state, task_state

if TYPE_CHECKING:
    from flowfield.application import Workspace

AttentionColumn = Literal["action", "waiting", "history"]


class AttentionItem(BaseModel):
    id: str
    kind: Literal["question", "review", "intervention", "integration", "result", "permission"]
    task_key: str | None
    title: str
    status: str
    updated_at: str
    code_available: bool | None = None
    run_id: str | None = None
    result_version: int | None = None
    next_action: str | None = None
    state: WorkState | None = None


class AttentionPage(BaseModel):
    items: list[AttentionItem]
    next_offset: int | None
    total: int


# Projection only: select short titles, never worker reports or question bodies.
_PROJECTION = """
WITH run_items AS (
    SELECT r.*, NOT EXISTS (
        SELECT 1 FROM runs n WHERE n.project_id=r.project_id
        AND n.task_id=r.task_id AND n.number>r.number
        AND json_extract(n.data,'$.purpose')=json_extract(r.data,'$.purpose')
    ) AS latest,
    EXISTS (SELECT 1 FROM runs p WHERE p.project_id=r.project_id
        AND p.task_id=r.task_id AND p.number<r.number
        AND json_extract(p.data,'$.purpose')=json_extract(r.data,'$.purpose')
        AND p.status IN ('changes_requested','failed','stopped','waiting_for_input')
    ) AS continuation
    FROM runs r WHERE r.project_id=:project
), items AS (
    SELECT p.id, 'permission' AS kind, t.key AS task_key,
        substr(json_extract(p.data,'$.title'),1,500) AS title, p.status,
        json_extract(p.data,'$.updated_at') AS updated_at, NULL AS code_available,
        CASE WHEN p.status='pending' THEN 'action' ELSE 'history' END AS bucket,
        json_extract(p.data,'$.run_id') AS run_id, NULL AS result_version
    FROM agent_permissions p LEFT JOIN tasks t ON t.project_id=p.project_id AND t.id=p.task_id
    WHERE p.project_id=:project
    UNION ALL
    SELECT q.id, 'question' AS kind, t.key AS task_key,
        substr(json_extract(q.data, '$.question'),1,500) AS title, q.status,
        json_extract(q.data, '$.updated_at') AS updated_at, NULL AS code_available,
        CASE q.status WHEN 'open' THEN 'action' WHEN 'answered' THEN 'waiting'
        ELSE 'history' END AS bucket, NULL AS run_id, NULL AS result_version
    FROM questions q LEFT JOIN tasks t ON t.project_id=q.project_id AND t.id=q.task_id
    WHERE q.project_id=:project
    UNION ALL
    SELECT r.id,
        CASE WHEN r.status IN ('in_review','accepted','changes_requested')
             OR json_extract(r.data, '$.predecessor_id') IS NOT NULL
             THEN 'review' ELSE 'intervention' END,
        t.key, substr(json_extract(t.data, '$.title'),1,500), r.status,
        coalesce(json_extract(r.data, '$.accepted_at'), json_extract(r.data, '$.ended_at'),
                 json_extract(r.data, '$.started_at'), json_extract(r.data, '$.created_at')),
        json_extract(r.data, '$.code_available'),
        CASE WHEN latest AND r.status IN ('in_review','failed','uncertain') THEN 'action'
             WHEN latest AND (r.status='changes_requested' OR
                 (continuation AND r.status IN ('preparing','running','stopping'))) THEN 'waiting'
             WHEN r.status IN ('accepted','stopped','failed','changes_requested')
                 OR (NOT latest AND r.status='waiting_for_input') THEN 'history'
             ELSE NULL END, r.id, NULL
    FROM run_items r JOIN tasks t ON t.project_id=r.project_id AND t.id=r.task_id
    WHERE NOT EXISTS (SELECT 1 FROM result_versions v WHERE v.run_id=r.id)
    UNION ALL
    SELECT v.id, 'result', t.key, substr(json_extract(t.data,'$.title'),1,500), v.status,
        coalesce(json_extract(v.data,'$.completed_at'), json_extract(v.data,'$.approved_at'),
                 json_extract(v.data,'$.created_at')),
        CASE WHEN json_extract(v.data,'$.completion')='code'
             THEN (SELECT json_extract(data,'$.code_available') FROM runs WHERE id=v.run_id)
             ELSE NULL END,
        CASE WHEN EXISTS (SELECT 1 FROM result_versions n WHERE n.project_id=v.project_id
                          AND n.task_id=v.task_id AND n.version>v.version) THEN 'history'
             WHEN EXISTS (
                 SELECT 1 FROM work_runs n WHERE n.project_id=v.project_id AND n.task_id=v.task_id
                 AND n.number>(SELECT number FROM runs WHERE id=v.run_id)) THEN 'history'
             WHEN EXISTS (SELECT 1 FROM task_replies q WHERE q.project_id=v.project_id
                 AND q.task_id=v.task_id AND json_extract(q.data,'$.status')='pending')
                 THEN 'waiting'
             WHEN v.status IN ('ready','blocked','stale','cancelled') THEN 'action'
             WHEN v.status='delivered' AND json_extract(v.data,'$.completion')='code'
                AND NOT (SELECT json_extract(data,'$.code_available') FROM runs WHERE id=v.run_id)
                THEN 'action'
             WHEN v.status IN ('preparing','delivering','changes_requested') THEN 'waiting'
             ELSE 'history' END, v.run_id, v.version
    FROM result_versions v JOIN tasks t ON t.project_id=v.project_id AND t.id=v.task_id
    WHERE v.project_id=:project
    UNION ALL
    SELECT i.id, 'integration', t.key, substr(json_extract(t.data, '$.title'),1,500),
        i.status, coalesce(json_extract(i.data,'$.completed_at'),
                           json_extract(i.data,'$.created_at')),
        NULL,
        CASE WHEN EXISTS (SELECT 1 FROM integrations newer WHERE newer.project_id=i.project_id
                          AND newer.run_id=i.run_id AND newer.number>i.number) THEN 'history'
             WHEN i.status IN ('failed','stale') THEN 'action'
             WHEN i.status IN ('preparing','ready','applying') THEN 'waiting'
             ELSE 'history' END, i.run_id, NULL
    FROM integrations i JOIN runs r ON r.id=i.run_id
    JOIN tasks t ON t.project_id=r.project_id AND t.id=r.task_id
    WHERE i.project_id=:project
    AND json_extract(i.data,'$.result_id') IS NULL
    AND NOT EXISTS (SELECT 1 FROM result_versions v
        WHERE json_extract(v.data,'$.integration_id')=i.id)
)
"""


def actionable_attention(db: sqlite3.Connection, project_id: str) -> Iterator[sqlite3.Row]:
    """Stream the complete actionable projection for transactional event observation."""
    yield from db.execute(
        _PROJECTION + "SELECT * FROM items WHERE bucket='action'", {"project": project_id}
    )


def attention_counts(db: sqlite3.Connection, project_id: str) -> dict[str, int]:
    """The board badge and inbox use exactly the same current-work projection."""
    return dict(
        db.execute(
            _PROJECTION
            + "SELECT bucket,count(*) FROM items WHERE bucket IS NOT NULL GROUP BY bucket",
            {"project": project_id},
        ).fetchall()
    )


def attention_page(
    workspace: "Workspace",
    project_id: str,
    column: AttentionColumn,
    offset: int = 0,
    limit: int = 20,
) -> AttentionPage:
    with workspace.connection() as db:
        workspace._project(db, project_id)
        args = {"project": project_id, "column": column, "offset": offset, "limit": limit}
        total = db.execute(
            _PROJECTION + "SELECT count(*) FROM items WHERE bucket=:column", args
        ).fetchone()[0]
        rows = db.execute(
            _PROJECTION + "SELECT id,kind,task_key,title,status,updated_at,code_available,run_id,"
            "result_version FROM items "
            "WHERE bucket=:column ORDER BY updated_at DESC,kind,id LIMIT :limit OFFSET :offset",
            args,
        ).fetchall()
        from flowfield.input_delivery import question_delivery

        items = [AttentionItem.model_validate(dict(row)) for row in rows]
        for item in items:
            if item.kind == "permission":
                item.state = WorkState(
                    label="Review tool permission"
                    if item.status == "pending"
                    else "Permission " + item.status,
                    tone="attention" if item.status == "pending" else "idle",
                    href=f"/projects/{project_id}/tasks/{item.task_key}"
                    if item.task_key
                    else f"/projects/{project_id}/inbox",
                )
            elif column != "history" and item.kind == "question":
                item.state = question_state(db, project_id, item.id)
            elif column != "history" and item.task_key:
                item.state = task_state(workspace, db, project_id, item.task_key)
            elif column != "history":
                item.state = WorkState(
                    label="Answer question" if item.status == "open" else "Resume coordinator",
                    tone="attention" if item.status == "open" else "waiting",
                )
            if item.kind == "result":
                from flowfield.result_actions import result_action
                from flowfield.result_models import ResultVersion

                saved = db.execute(
                    "SELECT data FROM result_versions WHERE id=?", (item.id,)
                ).fetchone()
                item.next_action = result_action(
                    workspace, db, ResultVersion.model_validate_json(saved[0])
                ).label
            if item.kind == "question":
                delivery = question_delivery(db, project_id, item.id)
                item.next_action = (
                    delivery.state
                    if delivery
                    else (
                        "Resume coordinator"
                        if item.status == "answered"
                        else "Answered"
                        if item.status == "applied"
                        else None
                    )
                )
        return AttentionPage(
            items=items,
            next_offset=offset + limit if offset + limit < total else None,
            total=total,
        )
