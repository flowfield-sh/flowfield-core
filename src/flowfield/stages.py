"""Shared coordinator/worker plan ownership, separate from task agreement revisions."""

import sqlite3

from flowfield.application import TaskRevision, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import ACTIVE, Run
from flowfield.stage_models import StagePlan, StageUpdate


class Stages:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _task(self, db: sqlite3.Connection, project_id: str, task_id: str) -> TaskRevision:
        identity = self.workspace._activity_scope(db, project_id, task_id)
        row = db.execute(
            "SELECT data FROM tasks WHERE project_id=? AND id=?", (project_id, identity)
        ).fetchone()
        return TaskRevision.model_validate_json(row[0])

    def _get(self, db: sqlite3.Connection, task: TaskRevision) -> StagePlan:
        row = db.execute(
            "SELECT data FROM stage_plans WHERE project_id=? AND task_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (task.project_id, task.id),
        ).fetchone()
        return (
            StagePlan.model_validate_json(row[0])
            if row
            else StagePlan(
                project_id=task.project_id,
                task_id=task.id,
                agreement_revision=task.agreement_revision,
            )
        )

    def get(self, project_id: str, task_id: str) -> StagePlan:
        with self.workspace.connection() as db:
            return self._get(db, self._task(db, project_id, task_id))

    def update(
        self, project_id: str, task_id: str, request: StageUpdate, *, run_id: str | None = None
    ) -> StagePlan:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            return self._update(db, project_id, task_id, request, run_id=run_id)

    def _update(
        self,
        db: sqlite3.Connection,
        project_id: str,
        task_id: str,
        request: StageUpdate,
        *,
        run_id: str | None = None,
        revising_agreement: bool = False,
    ) -> StagePlan:
        task = self._task(db, project_id, task_id)
        current = self._get(db, task)
        self.workspace._current(task.agreement_revision, request.agreement_revision)
        if (
            task.archived
            or task.status == "done"
            or (task.reconciliation_reason and not (revising_agreement and run_id is None))
        ):
            raise ApplicationError(
                "plan_closed", "Reconcile or reopen this task before planning.", 409
            )
        owner = db.execute(
            "SELECT data FROM runs WHERE project_id=? AND task_id=? AND status IN (?,?,?,?)",
            (project_id, task.id, *ACTIVE),
        ).fetchone()
        if run_id is not None:
            run = Run.model_validate_json(owner[0]) if owner else None
            if not run or run.id != run_id or run.status != "running" or run.result:
                raise ApplicationError(
                    "worker_scope_closed", "This worker no longer owns progress.", 409
                )
            if run.agreement_revision != task.agreement_revision:
                raise ApplicationError(
                    "assignment_changed", "Reconcile changed scope before progressing.", 409
                )
            if current.stages and current.agreement_revision != task.agreement_revision:
                raise ApplicationError(
                    "plan_scope_changed",
                    "Ask to reconcile the plan with the new agreement.",
                    409,
                )
            # Refinements may add steps or revisit them, but cannot erase requirements.
            proposed = {stage.id: stage for stage in request.stages}
            for prior in current.stages:
                if prior.id not in proposed or proposed[prior.id].outcome != prior.outcome:
                    raise ApplicationError(
                        "plan_scope_changed",
                        f"Keep existing stage outcomes verbatim. Include stage {prior.id!r} "
                        f"with its unchanged outcome: {prior.outcome!r}. Change status and "
                        "put progress evidence in reason; ask the coordinator to reconcile "
                        "any scope change.",
                        409,
                    )
        elif owner:
            raise ApplicationError(
                "worker_owns_plan", "The active worker owns progress; wait until idle.", 409
            )
        else:
            proposed = {stage.id: stage for stage in request.stages}
            if current.agreement_revision == task.agreement_revision and any(
                prior.status != "completed"
                and (prior.id not in proposed or proposed[prior.id].outcome != prior.outcome)
                for prior in current.stages
            ):
                raise ApplicationError(
                    "plan_scope_changed",
                    "Revise agreed scope before removing unfinished outcomes.",
                    409,
                )
        if (
            run_id
            and current.run_id == run_id
            and current.revision == request.expected_revision + 1
            and current.stages == request.stages
            and current.reason == request.reason
        ):
            return current  # Lost receipt: no second history entry.
        self.workspace._current(current.revision, request.expected_revision)
        if (
            current.agreement_revision == request.agreement_revision
            and current.stages == request.stages
            and current.reason == request.reason
        ):
            return current  # An unchanged save is not a new stage event.
        result = StagePlan(
            project_id=project_id,
            task_id=task.id,
            revision=current.revision + 1,
            agreement_revision=task.agreement_revision,
            stages=request.stages,
            reason=request.reason,
            author="worker" if run_id else "coordinator",
            run_id=run_id,
            created_at=now(),
        )
        db.execute(
            "INSERT INTO stage_plans VALUES (?,?,?,?)",
            (project_id, task.id, result.revision, result.model_dump_json()),
        )
        return result
