"""Result-bound definition adoption without rewriting a Worker's frozen assignment."""

import json
import sqlite3

from flowfield.application import TaskRevision
from flowfield.errors import ApplicationError
from flowfield.execution_models import Run
from flowfield.result_models import ResultVersion


def definition(
    db: sqlite3.Connection, run: Run, result: ResultVersion | None = None
) -> TaskRevision | None:
    if result is None:
        row = db.execute(
            "SELECT run_id,json_extract(data,'$.reconciled_task_revision'),"
            "json_extract(data,'$.source_commit') FROM result_versions "
            "WHERE workspace_id=? AND task_id=? "
            "ORDER BY version DESC LIMIT 1",
            (run.workspace_id, run.task_id),
        ).fetchone()
        result_run, task_revision, source = row if row else (None, None, None)
    else:
        result_run, task_revision, source = (
            result.run_id,
            result.reconciled_task_revision,
            result.source_commit,
        )
    if result_run != run.id or task_revision is None:
        return None
    if source != run.result_commit:
        raise ApplicationError("assignment_changed", "The retained result source changed.", 409)
    row = db.execute(
        "SELECT data FROM task_revisions WHERE workspace_id=? AND task_id=? AND revision=?",
        (run.workspace_id, run.task_id, task_revision),
    ).fetchone()
    if not row:
        raise ApplicationError("assignment_changed", "The reconciled definition is missing.", 409)
    return TaskRevision.model_validate_json(row[0])


def revision(db: sqlite3.Connection, run: Run, result: ResultVersion | None = None) -> int:
    task = definition(db, run, result)
    return task.agreement_revision if task else run.agreement_revision


def assignment(db: sqlite3.Connection, run: Run, result: ResultVersion) -> dict[str, str]:
    saved = dict(
        json.loads(db.execute("SELECT assignment FROM runs WHERE id=?", (run.id,)).fetchone()[0])
    )
    task = definition(db, run, result)
    if task:
        agreement = json.loads(saved["agreement"])
        agreement.update(
            task_revision=task.revision,
            agreement_revision=task.agreement_revision,
            scope="Reconciled result definition; original Worker assignment is unchanged.",
        )
        saved.update(
            agreement=json.dumps(agreement),
            description=task.body,
            criteria=task.acceptance_criteria,
            title=f"{task.key}: {task.title}",
        )
    return saved
