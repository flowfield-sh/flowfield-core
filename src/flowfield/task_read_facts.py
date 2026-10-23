"""Current task inputs for collection reads; scoped to the caller's read transaction."""

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowfield.application import Workspace


@dataclass(frozen=True)
class TaskReadFacts:
    row: sqlite3.Row
    queue_enabled: bool


def read_task_facts(
    workspace: "Workspace", db: sqlite3.Connection, project: str, task: str | None = None
) -> dict[str, TaskReadFacts]:
    """Batch indexed current-record lookups without loading task bodies or old runs.

    Single-task readers use the same projection. Never retain these facts across a
    transaction or pass them to a mutation's authority checks.
    """
    from flowfield.execution import Execution

    dependency_join = "d.workspace_id=t.workspace_id AND d.key=p.value"
    rows = db.execute(
        "SELECT t.id,t.key,t.scheduling_problem,json_extract(t.data,'$.status') AS status, "
        "json_extract(t.data,'$.archived') AS archived, "
        "json_extract(t.data,'$.agreement_revision') AS agreement_revision, "
        "json_extract(t.data,'$.reconciliation_reason') AS reason, "
        "length(trim(json_extract(t.data,'$.body'),char(9)||char(10)||char(13)||' ')) "
        "AS has_description, "
        "a.id AS owner_id,a.status AS owner_status,l.id AS latest_id,l.status AS latest_status, "
        "v.id AS result_id, "
        "(SELECT id FROM questions WHERE workspace_id=t.workspace_id AND task_id=t.id "
        "AND status IN ('open','answered') ORDER BY number LIMIT 1) AS question_id, "
        "EXISTS(SELECT 1 FROM agent_permissions WHERE workspace_id=t.workspace_id AND task_id=t.id "
        "AND status='pending') AS permission_pending, "
        "EXISTS(SELECT 1 FROM task_replies WHERE workspace_id=t.workspace_id AND task_id=t.id "
        "AND json_extract(data,'$.status')='pending') AS reply_pending, "
        "EXISTS(SELECT 1 FROM json_each(t.data,'$.dependencies') p "
        f"JOIN tasks d ON {dependency_join} "
        "WHERE json_extract(d.data,'$.status')!='done' "
        "OR json_extract(d.data,'$.reconciliation_reason') IS NOT NULL "
        "OR NOT EXISTS (SELECT 1 FROM result_versions dv JOIN runs dr ON dr.id=dv.run_id "
        "WHERE dv.workspace_id=d.workspace_id AND dv.task_id=d.id AND dv.status='delivered' "
        "AND (d.project_id IS NOT t.project_id OR t.project_id IS NULL "
        "OR json_extract(dv.data,'$.completion')='report' "
        "OR json_extract(dr.data,'$.code_available')=1) "
        "AND json_extract(dr.data,'$.agreement_revision')="
        "json_extract(d.data,'$.agreement_revision') AND NOT EXISTS "
        "(SELECT 1 FROM result_versions newer WHERE newer.workspace_id=dv.workspace_id "
        "AND newer.task_id=dv.task_id AND newer.version>dv.version))) AS waiting_dependencies "
        "FROM tasks t "
        "LEFT JOIN runs a ON a.id=(SELECT id FROM runs WHERE workspace_id=t.workspace_id "
        "AND task_id=t.id AND status IN ('preparing','running','stopping','uncertain') "
        "ORDER BY number LIMIT 1) "
        "LEFT JOIN runs l ON l.id=(SELECT id FROM runs WHERE workspace_id=t.workspace_id "
        "AND task_id=t.id ORDER BY number DESC LIMIT 1) "
        "LEFT JOIN result_versions v ON v.id=(SELECT id FROM result_versions "
        "WHERE workspace_id=t.workspace_id AND task_id=t.id ORDER BY version DESC LIMIT 1) "
        "WHERE t.workspace_id=? "
        + ("AND (t.id=? OR t.key=? COLLATE NOCASE) ORDER BY t.id=? DESC" if task else ""),
        (project, task, task, task) if task else (project,),
    ).fetchall()
    if not rows:
        return {}
    enabled = Execution(workspace)._settings(db, project).enabled
    return {row["id"]: TaskReadFacts(row, enabled) for row in rows}
