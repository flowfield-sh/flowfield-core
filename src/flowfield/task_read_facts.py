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

    @property
    def stages_stale(self) -> bool:
        return bool(
            self.row["stage_count"] and self.row["stage_revision"] != self.row["agreement_revision"]
        )


def read_task_facts(
    workspace: "Workspace", db: sqlite3.Connection, project: str, task: str | None = None
) -> dict[str, TaskReadFacts]:
    """Batch indexed current-record lookups without loading task bodies or old runs.

    Single-task readers use the same projection. Never retain these facts across a
    transaction or pass them to a mutation's authority checks.
    """
    from flowfield.execution import Execution

    dependency_join = (
        "d.key=p.value"
        if workspace.schema_version >= 62
        else "d.project_id=t.project_id AND d.id=p.value"
    )
    verification = (
        "(SELECT status FROM milestone_verifications "
        "WHERE id=json_extract(t.data,'$.publication.verification_id'))"
        if workspace.schema_version >= 69
        else "NULL"
    )
    rows = db.execute(
        "SELECT t.id,t.key,json_extract(t.data,'$.status') AS status, "
        "json_extract(t.data,'$.archived') AS archived, "
        "json_extract(t.data,'$.agreement_revision') AS agreement_revision, "
        "json_extract(t.data,'$.reconciliation_reason') AS reason, "
        "length(trim(json_extract(t.data,'$.body'),char(9)||char(10)||char(13)||' ')) "
        "AS has_description, "
        "json_extract(s.data,'$.agreement_revision') AS stage_revision, "
        "json_array_length(s.data,'$.stages') AS stage_count, "
        f"{verification} AS verification_status, "
        "a.id AS owner_id,a.status AS owner_status,l.id AS latest_id,l.status AS latest_status, "
        "v.id AS result_id, "
        "(SELECT id FROM questions WHERE project_id=t.project_id AND task_id=t.id "
        "AND status IN ('open','answered') ORDER BY number LIMIT 1) AS question_id, "
        "EXISTS(SELECT 1 FROM agent_permissions WHERE project_id=t.project_id AND task_id=t.id "
        "AND status='pending') AS permission_pending, "
        "EXISTS(SELECT 1 FROM task_replies WHERE project_id=t.project_id AND task_id=t.id "
        "AND json_extract(data,'$.status')='pending') AS reply_pending, "
        "EXISTS(SELECT 1 FROM json_each(t.data,'$.dependencies') p "
        f"JOIN tasks d ON {dependency_join} "
        "WHERE json_extract(d.data,'$.status')!='done') AS waiting_dependencies "
        "FROM tasks t "
        "LEFT JOIN runs a ON a.project_id=t.project_id AND a.task_id=t.id "
        "AND a.status IN ('preparing','running','stopping','uncertain') "
        "LEFT JOIN runs l ON l.id=(SELECT id FROM runs WHERE project_id=t.project_id "
        "AND task_id=t.id ORDER BY number DESC LIMIT 1) "
        "LEFT JOIN result_versions v ON v.id=(SELECT id FROM result_versions "
        "WHERE project_id=t.project_id AND task_id=t.id ORDER BY version DESC LIMIT 1) "
        "LEFT JOIN stage_plans s ON s.project_id=t.project_id AND s.task_id=t.id "
        "AND s.revision=(SELECT max(revision) FROM stage_plans "
        "WHERE project_id=t.project_id AND task_id=t.id) WHERE t.project_id=? "
        + ("AND (t.id=? OR t.key=? COLLATE NOCASE) ORDER BY t.id=? DESC" if task else ""),
        (project, task, task, task) if task else (project,),
    ).fetchall()
    if not rows:
        return {}
    enabled = Execution(workspace)._settings(db, project).enabled
    return {row["id"]: TaskReadFacts(row, enabled) for row in rows}
