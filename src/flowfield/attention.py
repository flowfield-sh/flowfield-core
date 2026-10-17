"""Bounded, derived human attention; no second workflow or mutable inbox records."""

import sqlite3
from collections.abc import Iterator
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from flowfield.browser_links import project_href
from flowfield.document_updates import UPDATED_MILESTONES, UPDATED_SOURCES
from flowfield.work_state import WorkState, question_state, task_state

if TYPE_CHECKING:
    from flowfield.application import Workspace

AttentionColumn = Literal["action", "waiting", "history"]
RequestKind = Literal["question", "attempt", "integration", "result", "permission", "milestone"]


class AttentionItem(BaseModel):
    project_id: str | None
    id: str
    kind: Literal[
        "question",
        "review",
        "intervention",
        "integration",
        "result",
        "permission",
        "document",
        "milestone",
    ]
    task_key: str | None
    title: str
    status: str
    updated_at: str
    code_available: bool | None = None
    run_id: str | None = None
    result_version: int | None = None
    next_action: str | None = None
    state: WorkState | None = None
    request_href: str | None = None
    source_href: str | None = None
    source_key: str | None = None
    request_kind: RequestKind | None = None


class AttentionPage(BaseModel):
    items: list[AttentionItem]
    next_offset: int | None
    total: int


# Projection only: select short titles, never worker reports or question bodies.
_PROJECTION = """
WITH scope_projects AS (SELECT id FROM projects WHERE id=:project), run_items AS (
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
    FROM runs r WHERE r.project_id IN (SELECT id FROM scope_projects)
), items AS (
    SELECT p.project_id, p.id, 'permission' AS kind, t.key AS task_key,
        substr(json_extract(p.data,'$.title'),1,500) AS title, p.status,
        json_extract(p.data,'$.updated_at') AS updated_at, NULL AS code_available,
        CASE WHEN p.status='pending' THEN 'action' ELSE 'history' END AS bucket,
        json_extract(p.data,'$.run_id') AS run_id, NULL AS result_version
    FROM agent_permissions p LEFT JOIN tasks t ON t.project_id=p.project_id AND t.id=p.task_id
    WHERE p.project_id IN (SELECT id FROM scope_projects)
    UNION ALL
    SELECT q.project_id, q.id, 'question' AS kind, t.key AS task_key,
        substr(json_extract(q.data, '$.question'),1,500) AS title, q.status,
        json_extract(q.data, '$.updated_at') AS updated_at, NULL AS code_available,
        CASE q.status WHEN 'open' THEN 'action' WHEN 'answered' THEN 'waiting'
        ELSE 'history' END AS bucket, NULL AS run_id, NULL AS result_version
    FROM questions q LEFT JOIN tasks t ON t.project_id=q.project_id AND t.id=q.task_id
    WHERE q.project_id IN (SELECT id FROM scope_projects)
    UNION ALL
    SELECT r.project_id, r.id,
        CASE WHEN r.status IN ('in_review','accepted','changes_requested')
             OR json_extract(r.data, '$.predecessor_id') IS NOT NULL
             THEN 'review' ELSE 'intervention' END,
        t.key, substr(json_extract(t.data, '$.title'),1,500), r.status,
        coalesce(json_extract(r.data, '$.accepted_at'), json_extract(r.data, '$.ended_at'),
                 json_extract(r.data, '$.started_at'), json_extract(r.data, '$.created_at')),
        json_extract(r.data, '$.code_available'),
        CASE WHEN r.status='failed' AND
                  (json_extract(t.data,'$.archived') OR
                   json_extract(t.data,'$.status')='done') THEN 'history'
             WHEN latest AND r.status='failed' AND
                  json_extract(t.data,'$.status')='up_next' THEN 'history'
             WHEN latest AND r.status IN ('in_review','failed','uncertain') THEN 'action'
             WHEN latest AND (r.status='changes_requested' OR
                 (continuation AND r.status IN ('preparing','running','stopping'))) THEN 'waiting'
             WHEN r.status IN ('accepted','stopped','failed','changes_requested')
                 OR (NOT latest AND r.status='waiting_for_input') THEN 'history'
             ELSE NULL END, r.id, NULL
    FROM run_items r JOIN tasks t ON t.project_id=r.project_id AND t.id=r.task_id
    WHERE NOT EXISTS (SELECT 1 FROM result_versions v WHERE v.run_id=r.id)
    UNION ALL
    SELECT v.project_id, v.id, 'result', t.key,
        substr(json_extract(t.data,'$.title'),1,500), v.status,
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
             /* review ownership */
             WHEN v.status IN ('ready','blocked','stale','cancelled') THEN 'action'
             WHEN v.status='delivered' AND json_extract(v.data,'$.completion')='code'
                AND NOT (SELECT json_extract(data,'$.code_available') FROM runs WHERE id=v.run_id)
                THEN 'action'
             WHEN v.status IN ('preparing','delivering','changes_requested') THEN 'waiting'
             ELSE 'history' END, v.run_id, v.version
    FROM result_versions v JOIN tasks t ON t.project_id=v.project_id AND t.id=v.task_id
    WHERE v.project_id IN (SELECT id FROM scope_projects)
    UNION ALL
    SELECT i.project_id, i.id, 'integration', t.key, substr(json_extract(t.data, '$.title'),1,500),
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
    WHERE i.project_id IN (SELECT id FROM scope_projects)
    AND json_extract(i.data,'$.result_id') IS NULL
    AND NOT EXISTS (SELECT 1 FROM result_versions v
        WHERE json_extract(v.data,'$.integration_id')=i.id)
)
"""

# Informational source updates have no actionable notification lifecycle.
_WITH_DOCUMENTS = (
    _PROJECTION.rstrip()[:-1]
    + """
    UNION ALL
    SELECT project_id,task_id,'document',task_key,substr(task_title,1,500),
        CASE WHEN max(milestone_changed) THEN 'milestone_changed' ELSE 'newer_source' END,
        max(updated_at),NULL,'waiting',NULL,NULL
    FROM (SELECT project_id,task_id,task_key,task_title,updated_at,0 AS milestone_changed FROM ("""
    + UPDATED_SOURCES
    + ") UNION ALL SELECT *,1 AS milestone_changed FROM ("
    + UPDATED_MILESTONES
    + """)) WHERE project_id IN (SELECT id FROM scope_projects)
    GROUP BY project_id,task_id
)
"""
)

_WORKSPACE_PROJECTION = _WITH_DOCUMENTS.replace(
    "SELECT id FROM projects WHERE id=:project",
    "SELECT project_id AS id FROM workspace_scopes WHERE workspace_id=:workspace "
    "AND (:project IS NULL OR project_id=:project)",
).replace(
    "WHERE p.project_id IN (SELECT id FROM scope_projects)",
    "WHERE p.project_id IN (SELECT id FROM scope_projects) "
    "OR (p.workspace_id=:workspace AND :project IS NULL)",
)


_WORKSPACE_PROJECTION = (
    _WORKSPACE_PROJECTION.rstrip()[:-1]
    + """
    UNION ALL
    SELECT NULL,v.id,'milestone',json_extract(v.data,'$.milestone_key'),
        substr(json_extract(v.data,'$.title'),1,500),v.status,
        json_extract(v.data,'$.updated_at'),NULL,
        CASE WHEN v.status IN ('ready','delivery_blocked') THEN 'action'
        WHEN json_extract(v.data,'$.current')=1 AND
            json_extract(v.data,'$.review_status') IN ('failed','uncertain','stopped') THEN 'action'
        WHEN v.status IN ('verifying','preparing','promoting') THEN 'waiting'
        ELSE 'history' END,NULL,NULL
    FROM milestone_verifications v WHERE v.workspace_id=:workspace
        AND (:project IS NULL OR EXISTS (SELECT 1 FROM json_each(v.data,'$.tasks') t
            WHERE json_extract(t.value,'$.project_id')=:project)
            OR json_extract(v.data,'$.project_id')=:project)
)
"""
)


def workspace_attention_counts(db: sqlite3.Connection, workspace_id: str) -> dict[str, int]:
    return dict(
        db.execute(
            review_projection(db, _WORKSPACE_PROJECTION)
            + "SELECT bucket,count(*) FROM items WHERE bucket IS NOT NULL GROUP BY bucket",
            {"workspace": workspace_id, "project": None},
        ).fetchall()
    )


def workspace_attention_requests(db: sqlite3.Connection, workspace_id: str) -> dict[str, str]:
    """Newest actionable Request per work identity, in canonical Inbox order."""
    from flowfield.requests import request_href
    from flowfield.storage import version

    identity = "task_key"
    if version(db) >= 77:
        identity = """coalesce(task_key,
            (SELECT json_extract(v.data,'$.milestone_key') FROM questions q
             JOIN milestone_reviews r ON r.id=json_extract(q.data,'$.origin_review_id')
             JOIN milestone_verifications v ON v.id=r.verification_id
             WHERE items.kind='question' AND q.id=items.id AND v.workspace_id=:workspace),
            (SELECT m.key FROM milestone_deliveries d JOIN milestones m
             ON m.workspace_id=d.workspace_id AND m.id=d.milestone_id
             WHERE items.kind='question' AND d.workspace_id=:workspace AND
             json_extract(d.data,'$.review_plan_question_id')=items.id))"""
    return {
        row[0]: request_href(workspace_id, row[1], row[2], row[3])
        for row in db.execute(
            review_projection(db, _WORKSPACE_PROJECTION)
            + f", work_requests AS (SELECT {identity} AS work_key,kind,id,project_id,updated_at "
            "FROM items WHERE bucket='action'), ranked AS ("
            "SELECT *,row_number() OVER (PARTITION BY work_key "
            "ORDER BY updated_at DESC,kind,project_id,id) AS rank "
            "FROM work_requests WHERE work_key IS NOT NULL) "
            "SELECT work_key,kind,id,project_id FROM ranked WHERE rank=1 "
            "ORDER BY updated_at DESC,kind,project_id,id",
            {"workspace": workspace_id, "project": None},
        )
    }


def actionable_attention(db: sqlite3.Connection, project_id: str) -> Iterator[sqlite3.Row]:
    """Stream the complete actionable projection for transactional event observation."""
    yield from db.execute(
        review_projection(db, _PROJECTION) + "SELECT * FROM items WHERE bucket='action'",
        {"project": project_id},
    )


def attention_counts(db: sqlite3.Connection, project_id: str) -> dict[str, int]:
    """The board badge and inbox use exactly the same current-work projection."""
    return dict(
        db.execute(
            review_projection(db, _WITH_DOCUMENTS)
            + "SELECT bucket,count(*) FROM items WHERE bucket IS NOT NULL GROUP BY bucket",
            {"project": project_id},
        ).fetchall()
    )


def attention_page(
    workspace: "Workspace",
    project_id: str | None,
    column: AttentionColumn,
    offset: int = 0,
    limit: int = 20,
    *,
    workspace_id: str | None = None,
    include_answered: bool = False,
    request_kind: RequestKind | None = None,
    request_id: str | None = None,
    requests_only: bool = False,
) -> AttentionPage:
    with workspace.connection() as db:
        if workspace_id is not None:
            from flowfield.workspaces import Workspaces

            Workspaces.read(db, workspace_id)
            if project_id is not None:
                Workspaces.check_scope(db, workspace_id, project_id)
        elif project_id is not None:
            workspace._work_scope(db, project_id)
        else:
            raise ValueError("An attention scope is required")
        projection = review_projection(
            db, _WORKSPACE_PROJECTION if workspace_id is not None else _WITH_DOCUMENTS
        )
        if request_id:
            # Exact links outlive Inbox visibility and later result creation.
            projection = (
                projection.replace(
                    "WHERE NOT EXISTS (SELECT 1 FROM result_versions v WHERE v.run_id=r.id)", ""
                )
                .replace("AND json_extract(i.data,'$.result_id') IS NULL", "")
                .replace(
                    "AND NOT EXISTS (SELECT 1 FROM result_versions v\n"
                    "        WHERE json_extract(v.data,'$.integration_id')=i.id)",
                    "",
                )
            )
        args = {
            "project": project_id,
            "workspace": workspace_id,
            "column": column,
            "include_answered": include_answered and column == "history",
            "offset": offset,
            "limit": limit,
            "request_kind": request_kind,
            "request_id": request_id,
        }
        selection = (
            "(bucket=:column OR (:include_answered AND kind='question' AND status='answered'))"
        )
        if requests_only:
            selection += " AND kind!='document'"
        if request_id:
            selection = (
                "id=:request_id AND project_id IS :project AND "
                "(kind=:request_kind OR (:request_kind='attempt' AND "
                "kind IN ('intervention','review')))"
            )
        total = db.execute(
            projection + "SELECT count(*) FROM items WHERE " + selection, args
        ).fetchone()[0]
        rows = db.execute(
            projection
            + "SELECT project_id,id,kind,task_key,title,status,updated_at,code_available,run_id,"
            "result_version FROM items "
            "WHERE " + selection + " ORDER BY updated_at DESC,kind,project_id,id "
            "LIMIT :limit OFFSET :offset",
            args,
        ).fetchall()
        from flowfield.input_delivery import question_delivery

        items = [AttentionItem.model_validate(dict(row)) for row in rows]
        for item in items:
            owner = item.project_id
            from flowfield.requests import bind_links

            bind_links(db, item, workspace_id)
            if item.kind == "milestone":
                item.next_action = {
                    "preparing": "Preparing combined candidate",
                    "ready": "Review milestone",
                    "verifying": "Verifying milestone",
                    "promoting": "Promoting verified milestone",
                    "delivery_blocked": "Retry milestone promotion",
                    "failed": "Verification needs follow-up",
                    "stale": "Verification out of date",
                    "accepted": "Milestone accepted",
                    "changes_requested": "Changes requested",
                }[item.status]
                item.state = WorkState(
                    label=item.next_action,
                    tone="attention"
                    if item.status in {"failed", "delivery_blocked"}
                    else "waiting"
                    if item.status == "ready"
                    else "active"
                    if item.status in {"preparing", "verifying", "promoting"}
                    else "complete"
                    if item.status == "accepted"
                    else "idle",
                    href=f"/workspaces/{workspace_id}/milestones/{item.task_key}?verification={item.id}",
                )
            elif item.kind == "document" and owner:
                item.state = WorkState(
                    label="Milestone details changed"
                    if item.status == "milestone_changed"
                    else "Newer source revision available",
                    tone="idle",
                    href=f"{project_href(owner)}/tasks/{item.task_key}",
                )
            elif item.kind == "permission":
                item.state = WorkState(
                    label="Review tool permission"
                    if item.status == "pending"
                    else "Permission " + item.status,
                    tone="attention" if item.status == "pending" else "idle",
                    href=f"{project_href(owner)}/tasks/{item.task_key}"
                    if item.task_key and owner
                    else f"{project_href(owner)}/inbox"
                    if owner
                    else f"/workspaces/{workspace_id}/inbox",
                )
            elif column != "history" and item.kind == "question" and owner:
                item.state = question_state(db, owner, item.id)
            elif column != "history" and item.task_key and owner:
                item.state = task_state(workspace, db, owner, item.task_key)
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
            if item.kind == "question" and owner:
                delivery = question_delivery(db, owner, item.id)
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
            if item.state is None:
                item.state = WorkState(
                    label=item.status.replace("_", " ").capitalize(),
                    tone="complete"
                    if item.status in {"accepted", "delivered", "applied", "answered"}
                    else "attention"
                    if item.status in {"failed", "blocked", "uncertain", "stale"}
                    else "waiting"
                    if item.status in {"open", "pending", "ready", "in_review"}
                    else "idle",
                )
        return AttentionPage(
            items=items,
            next_offset=offset + limit if offset + limit < total else None,
            total=total,
        )


def review_projection(db: sqlite3.Connection, projection: str) -> str:
    from flowfield.storage import version

    if version(db) >= 80:
        projection = projection.replace(
            "SELECT id FROM projects WHERE id=:project",
            "SELECT id FROM work_scopes WHERE id=:project",
        )
    clause = ""
    if version(db) >= 75:
        clause = """WHEN (v.status='ready' OR
            json_extract(v.data,'$.problem_code')='partial_outcome') AND EXISTS (
            SELECT 1 FROM task_reviews review WHERE review.project_id=v.project_id
            AND review.task_id=v.task_id
            AND review.status IN ('queued','preparing','running','waiting')) THEN 'waiting'"""
    return projection.replace("/* review ownership */", clause)
