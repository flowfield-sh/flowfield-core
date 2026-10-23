"""Read-only reuse blockers for browser and scoped task summaries."""

import sqlite3

from flowfield.application import TaskReference, TaskRevision, Workspace
from flowfield.browser_links import workspace_href
from flowfield.result_actions import reuse_action
from flowfield.result_models import ResultAction, ResultVersion


class PendingCode(TaskReference):
    result_href: str | None = None
    reuse_action: ResultAction | None = None


def pending_code(
    workspace: Workspace,
    db: sqlite3.Connection,
    workspace_id: str,
    records: dict[str, TaskRevision],
    completed: set[str],
) -> dict[str, list[PendingCode]]:
    available = {
        row[0]
        for row in db.execute(
            "SELECT task_id FROM runs r WHERE workspace_id=? AND status='accepted' "
            "AND (json_extract(data,'$.code_available')=1 "
            "OR json_extract(data,'$.completion')='report') AND NOT EXISTS "
            "(SELECT 1 FROM runs newer WHERE newer.workspace_id=r.workspace_id "
            "AND newer.task_id=r.task_id AND newer.status='accepted' "
            "AND newer.number>r.number)",
            (workspace_id,),
        )
    }
    references: dict[str, PendingCode] = {}
    pending: dict[str, list[PendingCode]] = {}
    for task in records.values():
        if task.workspace_id != workspace_id or task.status not in ("backlog", "up_next"):
            continue
        pending[task.key] = []
        for key in task.dependencies:
            prerequisite = records[key]
            if (
                key not in completed
                or prerequisite.workspace_id != workspace_id
                or prerequisite.project_id != task.project_id
                or task.project_id is None
                or prerequisite.id in available
            ):
                continue
            if key not in references:
                reference = PendingCode(**prerequisite.model_dump())
                row = db.execute(
                    "SELECT data FROM result_versions WHERE workspace_id=? AND task_id=? "
                    "ORDER BY version DESC LIMIT 1",
                    (workspace_id, prerequisite.id),
                ).fetchone()
                if row:
                    version = ResultVersion.model_validate_json(row[0])
                    reference.reuse_action = reuse_action(workspace, db, version)
                    reference.result_href = f"{workspace_href(workspace_id)}/results/{version.id}"
                references[key] = reference
            pending[task.key].append(references[key])
    return pending
