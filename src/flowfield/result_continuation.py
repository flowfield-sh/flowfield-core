"""Bounded service continuation of unchanged work after destination movement."""

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from flowfield.adapters.git_workspace import git
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.integration import Integrations
from flowfield.result_models import ResultVersion

if TYPE_CHECKING:
    from flowfield.results import Results


def guard(results: "Results", db: sqlite3.Connection, version: ResultVersion) -> None:
    """Recheck automatic authority in the same transaction that schedules work."""
    if not results.execution._settings(db, version.workspace_id).enabled:
        raise ApplicationError("queue_paused", "The worker queue is paused.", 409)
    run = results._current(db, version)
    task = results.workspace._task(db, version.workspace_id, version.task_id)
    results.execution._current_assignment(db, run, task)
    if db.execute(
        "SELECT 1 FROM task_reviews WHERE workspace_id=? AND task_id=? "
        "AND status IN ('preparing','running','waiting','uncertain')",
        (version.workspace_id, version.task_id),
    ).fetchone():
        raise ApplicationError(
            "review_owns_result", "The current review still owns this task.", 409
        )
    if (
        not task.project_id
        or Integrations(results.workspace)._settings(db, task.project_id).revision
        != version.settings_revision
    ):
        raise ApplicationError(
            "settings_changed", "Changed settings require deliberate preparation.", 409
        )


def continue_available(results: "Results", workspace_id: str) -> None:
    """Schedule at most one operation. Existing queue/reviewer owners perform it."""
    if not results.execution.settings(workspace_id).enabled:
        return
    with results.workspace.connection() as db:
        rows = db.execute(
            "SELECT v.data FROM result_versions v WHERE workspace_id=? "
            "AND status IN ('stale','blocked') AND json_extract(data,'$.completion')='code' "
            "AND json_extract(data,'$.problem_code') IN "
            "('target_changed','integration_conflict','checks_failed') "
            "AND NOT EXISTS (SELECT 1 FROM result_versions n WHERE n.workspace_id=v.workspace_id "
            "AND n.task_id=v.task_id AND n.version>v.version) ORDER BY number",
            (workspace_id,),
        ).fetchall()
    for row in rows:
        version = ResultVersion.model_validate_json(row[0])
        try:
            with results.workspace.connection() as db:
                guard(results, db, version)
                run = results._current(db, version)
            request = RunAction(expected_revision=version.revision, author="service")
            if version.problem_code == "target_changed":
                results.reprepare(workspace_id, version.id, request, automatic=True)
                return
            # Original worker failures are not evidence of a destination conflict.
            if not version.target_before or version.target_before == run.base_commit:
                continue
            settings = Integrations(results.workspace).for_task(workspace_id, version.task_id)
            if (
                Integrations(results.workspace).head(workspace_id, version.task_id)
                != version.target_before
            ):
                results.reprepare(workspace_id, version.id, request, automatic=True)
                return
            assert version.source_commit
            repository = Path(results.workspace.project(settings.project_id).path)
            with results.workspace.connection() as db:
                previous = [
                    ResultVersion.model_validate_json(r[0])
                    for r in db.execute(
                        "SELECT data FROM result_versions WHERE workspace_id=? AND task_id=? "
                        "AND json_extract(data,'$.correction.target_commit')=? "
                        "ORDER BY version DESC LIMIT 8",
                        (workspace_id, version.task_id, version.target_before),
                    )
                ]
            tree = git(repository, "rev-parse", version.source_commit + "^{tree}")
            repeated = any(
                old.correction
                and git(repository, "rev-parse", old.correction.source_commit + "^{tree}") == tree
                for old in previous
            )
            if repeated:
                with results.workspace.connection(write=True, workspace_id=workspace_id) as db:
                    latest = results._get(db, workspace_id, version.id)
                    results.workspace._current(latest.revision, version.revision)
                    guard(results, db, latest)
                    latest.problem_code = "correction_no_progress"
                    latest.problem = (latest.problem or "Correction needs attention.") + (
                        " Automatic continuation paused: the correction repeated unchanged code."
                    )
                    results._save(db, latest)
                return
            request.note = (
                "Correct the failure against the advanced destination "
                "within the published assignment."
            )
            results.correct(workspace_id, version.id, request, automatic=True)
            return
        except ApplicationError:
            # Paused/changed/blocked work retains its exact actionable evidence.
            continue
