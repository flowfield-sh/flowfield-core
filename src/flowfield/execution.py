"""Transactional queue, attempt ownership and review. No harness or OS operations."""

import json
import sqlite3
from typing import Any
from uuid import uuid4

from flowfield.agent_models import AgentChoice
from flowfield.agent_settings import AgentSettings
from flowfield.application import Task, TaskRevision, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import (
    ACTIVE,
    CheckResult,
    QueueEdit,
    Run,
    RunAction,
    RunPage,
    RunStatus,
    SettingsEdit,
    Usage,
    WorkerOccupancy,
    WorkerResult,
    WorkerSettings,
)
from flowfield.harness_models import HarnessLaunch
from flowfield.publication import stages_need_reconciliation
from flowfield.questions import Question, QuestionCreate, Questions


class Execution:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _settings(self, db: sqlite3.Connection, project_id: str) -> WorkerSettings:
        self.workspace._project(db, project_id)
        row = db.execute(
            "SELECT data FROM worker_settings WHERE project_id=?", (project_id,)
        ).fetchone()
        return (
            WorkerSettings.model_validate_json(row[0])
            if row
            else WorkerSettings(project_id=project_id)
        )

    def settings(self, project_id: str) -> WorkerSettings:
        with self.workspace.connection() as db:
            return self._settings(db, project_id)

    def occupancy(self, project_id: str) -> WorkerOccupancy:
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            counts = dict(
                db.execute(
                    "SELECT status, count(*) FROM runs WHERE project_id=? GROUP BY status",
                    (project_id,),
                ).fetchall()
            )
            return WorkerOccupancy(
                active=sum(
                    counts.get(status, 0) for status in ("preparing", "running", "stopping")
                ),
                uncertain=counts.get("uncertain", 0),
            )

    def _save_settings(self, db: sqlite3.Connection, settings: WorkerSettings) -> None:
        settings.revision += 1
        db.execute(
            (
                "INSERT INTO worker_settings VALUES (?, ?) ON CONFLICT(project_id)"
                " DO UPDATE SET data=excluded.data"
            ),
            (settings.project_id, settings.model_dump_json()),
        )

    def configure(self, project_id: str, request: SettingsEdit) -> WorkerSettings:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            return self._configure(db, project_id, request)

    def _configure(
        self, db: sqlite3.Connection, project_id: str, request: SettingsEdit
    ) -> WorkerSettings:
        if request.selection.fast:
            raise ApplicationError(
                "worker_speed_override",
                "Set worker Fast mode on the individual task; project workers use normal speed.",
                409,
            )
        settings = self._settings(db, project_id)
        self.workspace._current(settings.revision, request.expected_revision)
        settings.selection = request.selection.model_copy(deep=True)
        if settings.selection.fast is None:
            settings.selection.fast = False
        settings.max_parallel, settings.problem = request.max_parallel, None
        self._save_settings(db, settings)
        return settings

    def queue(self, project_id: str, request: QueueEdit) -> WorkerSettings:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            settings = self._settings(db, project_id)
            self.workspace._current(settings.revision, request.expected_revision)
            if request.enabled and not settings.selection:
                raise ApplicationError(
                    "worker_model_required",
                    (
                        "Choose a worker harness and model in Project details, or"
                        " ask the coordinator to configure them."
                    ),
                    409,
                )
            settings.enabled, settings.problem = request.enabled, None
            self._save_settings(db, settings)
            return settings

    def queue_problem(self, project_id: str, problem: str | None) -> None:
        if self.settings(project_id).problem == problem:
            return
        with self.workspace.connection(write=True, project_id=project_id) as db:
            settings = self._settings(db, project_id)
            settings.problem = problem
            self._save_settings(db, settings)

    def _run(self, db: sqlite3.Connection, project_id: str, run_id: str) -> Run:
        row = db.execute(
            "SELECT data FROM runs WHERE project_id=? AND id=?", (project_id, run_id)
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Attempt not found in this project.", 404)
        return Run.model_validate_json(row[0])

    def get(self, project_id: str, run_id: str) -> Run:
        with self.workspace.connection() as db:
            return self._run(db, project_id, run_id)

    def _save(self, db: sqlite3.Connection, run: Run) -> None:
        run.revision += 1
        db.execute(
            "UPDATE runs SET status=?, data=? WHERE id=?",
            (run.status, run.model_dump_json(), run.id),
        )

    def _correction_count(self, db: sqlite3.Connection, project_id: str, task_id: str) -> int:
        # Input continuations retain their correction number; explicit retries consume another.
        return int(
            db.execute(
                "SELECT coalesce(max(json_extract(data,'$.correction.number')),0) "
                "FROM runs WHERE project_id=? AND task_id=?",
                (project_id, task_id),
            ).fetchone()[0]
        )

    def page(
        self,
        project_id: str,
        *,
        task_id: str | None = None,
        before: int | None = None,
        limit: int = 20,
        attention: bool = False,
    ) -> RunPage:
        limit = max(1, min(limit, 50))
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            if task_id:
                task_id = self.workspace._task(db, project_id, task_id).id
            rows = db.execute(
                (
                    "SELECT number, data FROM runs r WHERE project_id=? AND (? IS NULL"
                    " OR task_id=?) AND (? IS NULL OR number<?) "
                    "AND (?=0 OR (status IN ('in_review','failed','uncertain') AND NOT"
                    " EXISTS (SELECT 1 FROM runs n WHERE n.project_id=r.project_id AND"
                    " n.task_id=r.task_id AND n.number>r.number))) ORDER BY number "
                    "DESC LIMIT ?"
                ),
                (project_id, task_id, task_id, before, before, int(attention), limit + 1),
            ).fetchall()
            return RunPage(
                items=[Run.model_validate_json(r["data"]) for r in rows[:limit]],
                next_before=rows[limit - 1]["number"] if len(rows) > limit else None,
            )

    def active(self) -> list[Run]:
        with self.workspace.connection() as db:
            return [
                Run.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT data FROM runs WHERE status IN "
                    "('preparing','running','stopping','uncertain')"
                )
            ]

    def _task_state(
        self, db: sqlite3.Connection, task: Task, status: str, note: str, author: str = "flowfield"
    ) -> None:
        values = task.model_dump()
        values.update(
            status=status,
            revision=task.revision + 1,
            updated_at=now(),
            updated_by=author,
            change_note=note,
        )
        if status == "up_next":
            values["reconciliation_reason"] = None
        self.workspace._apply_task(db, TaskRevision(**values))

    def _current_assignment(self, run: Run, task: Task) -> None:
        if (
            task.archived
            or task.readiness != "ready"
            or task.agreement_revision != run.agreement_revision
            or not task.publication
            or task.publication.completion != run.completion
            or task.publication.target_branch != run.target_branch
        ):
            raise ApplicationError(
                "assignment_changed",
                (
                    "This result belongs to an older or blocked assignment. Reconcile "
                    "the task and request a revised attempt before accepting."
                ),
                409,
            )

    def claim(
        self, project_id: str, baseline: str, available_results: dict[str, set[str]]
    ) -> Run | None:
        """Reserve once against fresh state; caller supplies verified immutable Git facts."""
        with self.workspace.connection(write=True, project_id=project_id) as db:
            settings = self._settings(db, project_id)
            count = db.execute(
                (
                    "SELECT count(*) FROM runs WHERE project_id=? AND status IN "
                    "('preparing','running','stopping','uncertain')"
                ),
                (project_id,),
            ).fetchone()[0]
            if not settings.enabled or not settings.selection or count >= settings.max_parallel:
                return None
            project = self.workspace._project(db, project_id)
            waiting_for_code = []
            exhausted_corrections = []
            for task in self.workspace._tasks(db, project_id, False):
                previous = db.execute(
                    "SELECT data FROM work_runs WHERE project_id=? AND task_id=? "
                    "ORDER BY number DESC LIMIT 1",
                    (project_id, task.id),
                ).fetchone()
                predecessor = Run.model_validate_json(previous[0]) if previous else None
                questions = Questions(self.workspace)
                pending_input = (
                    questions._get(db, project_id, predecessor.question_id)
                    if predecessor
                    and predecessor.status == "waiting_for_input"
                    and predecessor.question_id
                    and predecessor.input_checkpoint
                    else None
                )
                continuation = bool(
                    predecessor
                    and pending_input
                    and pending_input.status == "answered"
                    and not pending_input.continuation_run_id
                    and pending_input.origin_run_id == predecessor.id
                    and task.status in ("in_progress", "up_next")
                    and task.agreement_revision == predecessor.agreement_revision
                    and not task.reconciliation_reason
                    and not task.blocked_by
                    and all(q.id == pending_input.id for q in task.blocking_questions)
                )
                # A continuation also needs a stage plan reconciled to its frozen agreement.
                if stages_need_reconciliation(db, project_id, task.id, task.agreement_revision):
                    continue
                if (
                    not continuation and (task.status != "up_next" or task.readiness != "ready")
                ) or task.publication_status != "published":
                    continue
                if db.execute(
                    (
                        "SELECT 1 FROM runs WHERE project_id=? AND task_id=? AND status IN"
                        " ('preparing','running','stopping','uncertain')"
                    ),
                    (project_id, task.id),
                ).fetchone():
                    continue
                correction = predecessor.next_correction if predecessor else None
                if predecessor and predecessor.status in ("failed", "stopped", "waiting_for_input"):
                    correction = predecessor.correction
                if correction and not continuation:
                    correction_count = self._correction_count(db, project_id, task.id)
                    if correction_count >= 2:
                        exhausted_corrections.append(task.key)
                        continue
                    correction = correction.model_copy(update={"number": correction_count + 1})
                chosen_base = (
                    correction.base_commit
                    if correction
                    else predecessor.result_commit
                    if predecessor
                    and predecessor.status == "changes_requested"
                    and predecessor.result_commit
                    else baseline
                )
                retrying_input = bool(
                    predecessor
                    and predecessor.input_question_id
                    and predecessor.status in ("failed", "stopped")
                    and task.agreement_revision == predecessor.agreement_revision
                    and task.publication
                    and task.publication.completion == predecessor.completion
                    and task.publication.target_branch == predecessor.target_branch
                )
                if continuation or retrying_input:
                    assert predecessor
                    chosen_base = predecessor.base_commit
                input_base = (
                    predecessor.input_checkpoint
                    if continuation and predecessor
                    else predecessor.input_base_commit
                    if retrying_input and predecessor
                    else None
                )
                prerequisites: list[dict[str, Any]] = []
                usable = True
                for dependency in task.dependencies:
                    row = db.execute(
                        (
                            "SELECT data FROM work_runs WHERE project_id=? AND task_id=? AND "
                            "status='accepted' ORDER BY number DESC LIMIT 1"
                        ),
                        (project_id, dependency),
                    ).fetchone()
                    if row:
                        prerequisite = Run.model_validate_json(row[0])
                        if (
                            prerequisite.completion == "code"
                            and prerequisite.result_commit
                            not in available_results.get(input_base or chosen_base, set())
                        ):
                            usable = False
                        prerequisites.append(
                            {
                                "task": prerequisite.task_key,
                                "run_id": prerequisite.id,
                                "completion": prerequisite.completion,
                                "agreement_revision": prerequisite.agreement_revision,
                                "commit": prerequisite.result_commit
                                if prerequisite.completion == "code"
                                else None,
                                "result": prerequisite.result.model_dump()
                                if prerequisite.result
                                else None,
                            }
                        )
                    else:
                        prerequisite_task = self.workspace._task(db, project_id, dependency)
                        if prerequisite_task.report_completion_revision:
                            accepted = db.execute(
                                "SELECT data FROM task_revisions WHERE project_id=? "
                                "AND task_id=? AND revision=?",
                                (
                                    project_id,
                                    dependency,
                                    prerequisite_task.report_completion_revision,
                                ),
                            ).fetchone()
                            assert accepted
                            recorded = json.loads(accepted[0])
                            handoff = db.execute(
                                "SELECT id,body FROM activity WHERE project_id=? AND task_id=? "
                                "AND kind='handoff' AND created_at<=? "
                                "ORDER BY sequence DESC LIMIT 1",
                                (project_id, dependency, recorded["updated_at"]),
                            ).fetchone()
                            prerequisites.append(
                                {
                                    "task": prerequisite_task.key,
                                    "commit": None,
                                    "completion": "report",
                                    "accepted_by": recorded["updated_by"],
                                    "task_revision": prerequisite_task.report_completion_revision,
                                    "description": recorded["body"],
                                    "result": {
                                        "summary": handoff["body"]
                                        if handoff
                                        else "Human accepted a report; no findings were recorded."
                                    },
                                    "source": handoff["id"] if handoff else None,
                                }
                            )
                        else:
                            usable = False
                if not usable:
                    waiting_for_code.append(task.key)
                    continue
                assert task.publication
                runtime_row = db.execute(
                    "SELECT data FROM integration_settings WHERE project_id=?", (project_id,)
                ).fetchone()
                runtime_settings = json.loads(runtime_row[0]) if runtime_row else {}
                if (
                    (continuation or retrying_input)
                    and predecessor
                    and (
                        task.publication.completion != predecessor.completion
                        or task.publication.target_branch != predecessor.target_branch
                        or (
                            predecessor.completion == "code"
                            and runtime_settings.get("target_branch") != predecessor.target_branch
                        )
                    )
                ):
                    continue
                effective = (
                    AgentSettings(self.workspace)
                    .resolve(db, project_id, "worker", task.id)
                    .effective
                )
                assert effective
                effective = AgentSettings(self.workspace).freeze(db, effective)
                run = Run(
                    id=uuid4().hex,
                    project_id=project_id,
                    task_id=task.id,
                    task_key=task.key,
                    environment_id=uuid4().hex,
                    predecessor_id=predecessor.id if predecessor else None,
                    input_question_id=pending_input.id
                    if continuation and pending_input
                    else predecessor.input_question_id
                    if retrying_input and predecessor
                    else None,
                    input_base_commit=input_base,
                    correction=correction,
                    runtime=runtime_settings.get("runtime", "local"),
                    setup_commands=runtime_settings.get("setup_commands", []),
                    setup_timeout_seconds=runtime_settings.get("setup_timeout_seconds", 120),
                    model=effective.choice.model,
                    effort=effective.choice.effort,
                    agent_settings=effective,
                    agreement_revision=task.agreement_revision,
                    base_commit=chosen_base,
                    completion=task.publication.completion,
                    target_branch=task.publication.target_branch,
                    feedback=predecessor.feedback if predecessor else "",
                    created_at=now(),
                )
                milestone = (
                    self.workspace._milestone(db, project_id, task.milestone_id)
                    if task.milestone_id
                    else None
                )
                assignment = {
                    "task_type": task.task_type,
                    "agreement": json.dumps(
                        {
                            "task": task.key,
                            "task_revision": task.revision,
                            "agreement_revision": task.agreement_revision,
                            "completion": task.publication.completion,
                            "target_branch": task.publication.target_branch,
                            "base_commit": chosen_base,
                            "scope": "Frozen at claim; discoveries do not change intent.",
                        }
                    ),
                    "description": task.body,
                    "project": project.description,
                    "milestone": milestone.body if milestone else "",
                    "prerequisites": json.dumps(prerequisites),
                    "feedback": run.feedback,
                    "predecessor": predecessor.result.model_dump_json()
                    if predecessor and predecessor.result
                    else "",
                    "title": f"{task.key}: {task.title}",
                    "correction": correction.model_dump_json() if correction else "",
                    "validation": json.dumps(runtime_settings),
                }
                handoff = db.execute(
                    "SELECT body FROM activity WHERE project_id=? AND task_id=? "
                    "AND kind='handoff' ORDER BY sequence DESC LIMIT 1",
                    (project_id, task.id),
                ).fetchone()
                assignment["handoff"] = handoff[0] if handoff else ""
                assignment["questions"] = json.dumps(
                    [
                        json.loads(row[0])
                        for row in db.execute(
                            "SELECT data FROM questions WHERE project_id=? AND task_id=? "
                            "AND status IN ('open','answered') ORDER BY number",
                            (project_id, task.id),
                        )
                    ]
                )
                if (
                    predecessor
                    and not continuation
                    and not retrying_input
                    and predecessor.agreement_revision == run.agreement_revision
                ):
                    previous_sections = json.loads(
                        db.execute(
                            "SELECT assignment FROM runs WHERE id=?", (predecessor.id,)
                        ).fetchone()[0]
                    )
                    for section in ("input", "exchange_history", "earlier_answers"):
                        if section in previous_sections:
                            assignment[section] = previous_sections[section]
                if continuation or retrying_input:
                    assert predecessor
                    assignment = json.loads(
                        db.execute(
                            "SELECT assignment FROM runs WHERE id=?",
                            (predecessor.id,),
                        ).fetchone()[0]
                    )
                    assignment["validation"] = json.dumps(runtime_settings)
                    assignment["correction"] = correction.model_dump_json() if correction else ""
                    if continuation:
                        assert pending_input
                        from flowfield.worker_context import continue_input

                        continue_input(
                            assignment,
                            {
                                "question_id": pending_input.id,
                                "origin_run_id": predecessor.id,
                                "answer_revision": pending_input.revision,
                                "question": pending_input.question,
                                "context": pending_input.context,
                                "answer": pending_input.answer,
                                "checkpoint": input_base,
                            },
                        )
                        question = questions._change(
                            pending_input,
                            pending_input.revision,
                            "flowfield",
                            status="assigned",
                            continuation_run_id=run.id,
                            consumed_answer_revision=pending_input.revision,
                        )
                        questions._save(
                            db,
                            question,
                            "Answer assigned to a continuation; model use is not yet confirmed.",
                        )
                from flowfield.worker_context import enrich

                enrich(db, run, assignment)
                db.execute(
                    (
                        "INSERT INTO runs(id,project_id,task_id,status,data,assignment) "
                        "VALUES (?,?,?,?,?,?)"
                    ),
                    (
                        run.id,
                        project_id,
                        task.id,
                        run.status,
                        run.model_dump_json(),
                        json.dumps(assignment),
                    ),
                )
                self._task_state(
                    db,
                    task,
                    "in_progress",
                    f"Attempt {run.id[:8]} reserved at {run.base_commit[:12]}.",
                )
                return run
            if exhausted_corrections:
                raise ApplicationError(
                    "correction_limit",
                    ", ".join(exhausted_corrections[:5])
                    + " used two corrections. Reconcile outcome and scope with the coordinator.",
                    409,
                )
            if waiting_for_code:
                raise ApplicationError(
                    "prerequisite_code_unavailable",
                    ", ".join(waiting_for_code[:5])
                    + " waits for accepted prerequisite code in the project baseline. "
                    "Integrate those results before dependent work can start.",
                    409,
                )
            return None

    def assignment(self, project_id: str, run_id: str) -> dict[str, str]:
        with self.workspace.connection() as db:
            self._run(db, project_id, run_id)
            value: dict[str, str] = json.loads(
                db.execute("SELECT assignment FROM runs WHERE id=?", (run_id,)).fetchone()[0]
            )
            return value

    def worker_access(self, project_id: str, run_id: str) -> Run:
        run = self.get(project_id, run_id)
        if run.status != "running":
            raise ApplicationError(
                "worker_scope_closed", "This attempt no longer owns a running assignment.", 409
            )
        return run

    def context_section(self, project_id: str, run_id: str, section: str) -> str:
        from flowfield.worker_context import attempt_source

        with self.workspace.connection() as db:
            run = self._run(db, project_id, run_id)
            sections = json.loads(
                db.execute("SELECT assignment FROM runs WHERE id=?", (run_id,)).fetchone()[0]
            )
            if section.startswith("attempt:"):
                return attempt_source(db, run, sections, section.removeprefix("attempt:"))
            if section not in sections:
                raise ApplicationError(
                    "unknown_section", "Available sections: " + ", ".join(sections)
                )
            return str(sections[section])

    def report_result(self, project_id: str, run_id: str, result: WorkerResult) -> None:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            if run.status != "running" or run.result:
                raise ApplicationError("worker_scope_closed", "This report is closed.", 409)
            if result.outcome == "complete":
                from flowfield.stages import Stages

                task = self.workspace._task(db, project_id, run.task_id)
                plan = Stages(self.workspace)._get(db, task)
                unfinished = [stage.id for stage in plan.stages if stage.status != "completed"]
                if unfinished:
                    raise ApplicationError(
                        "plan_progress_unfinished",
                        "Before claiming a complete outcome, use update_stages to record actual "
                        "progress for: " + ", ".join(unfinished) + ". Keep outcomes verbatim. "
                        "If that work remains unfinished, submit a partial outcome instead.",
                        409,
                    )
            run.result = result
            self._save(db, run)

    def excluded_files(self, project_id: str, run_id: str, paths: list[str]) -> None:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            run.excluded_files = paths[:100]
            self._save(db, run)

    def ask_question(self, project_id: str, run_id: str, request: QuestionCreate) -> Question:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            if run.status != "running" or run.result or request.task_id != run.task_id:
                raise ApplicationError("worker_scope_closed", "This assignment is closed.", 409)
            if run.question_id:
                return Questions(self.workspace)._get(db, project_id, run.question_id)
            question = Questions(self.workspace)._ask(db, project_id, request, origin_run_id=run.id)
            run.question_id = question.id
            self._save(db, run)
            return question

    def started(
        self,
        project_id: str,
        run_id: str,
        *,
        applied_agent: AgentChoice | None = None,
        harness_launch: HarnessLaunch | None = None,
    ) -> Run:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            if run.status != "preparing":
                raise ApplicationError("run_changed", "Attempt no longer awaits launch.", 409)
            task = self.workspace._task(db, project_id, run.task_id)
            self._current_assignment(run, task)
            run.applied_agent = applied_agent
            run.harness_launch = harness_launch
            run.status, run.started_at = "running", now()
            self._save(db, run)
            return run

    def finish(
        self,
        project_id: str,
        run_id: str,
        status: RunStatus,
        *,
        result: WorkerResult | None = None,
        commit: str | None = None,
        input_checkpoint: str | None = None,
        problem: str | None = None,
    ) -> Run:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            if run.status not in ACTIVE:
                raise ApplicationError(
                    "run_changed", "A late result cannot replace a finished attempt.", 409
                )
            if run.status == "stopping" and status != "uncertain":
                status, problem = (
                    "stopped",
                    "Stopped before result submission completed. Work preserved.",
                )
            if status == "in_review" and (not result or not commit):
                raise ApplicationError(
                    "result_missing", "Review requires a result and immutable code reference."
                )
            run.status, run.result_commit, run.problem = status, commit, problem
            if status == "waiting_for_input":
                run.input_checkpoint = input_checkpoint
            run.result = result or run.result
            run.ended_at = now() if status != "uncertain" else None
            self._save(db, run)
            task = self.workspace._task(db, project_id, run.task_id)
            if status == "in_review":
                from flowfield.results import Results

                Results(self.workspace).capture(db, run)
                self._task_state(
                    db,
                    task,
                    "in_review",
                    f"Attempt {run.id[:8]} submitted {commit}. "
                    "Preparing the proposed result for review.",
                )
            return run

    def stop_requested(self, project_id: str, run_id: str, request: RunAction) -> Run:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            # Stop targets this immutable attempt, not its changing usage/progress snapshot.
            # Never retarget a successor; future revisions still indicate an invalid request.
            if request.expected_revision > run.revision:
                self.workspace._current(run.revision, request.expected_revision)
            if run.status in ("stopping", "stopped"):
                return run
            if run.status == "waiting_for_input" and run.question_id:
                run.status, run.problem = (
                    "stopped",
                    "Continuation stopped; your answer and unfinished work are preserved.",
                )
                self._save(db, run)
                return run
            if run.status not in ACTIVE:
                raise ApplicationError("not_running", "This attempt is no longer running.", 409)
            run.status = "stopping"
            self._save(db, run)
            return run

    def retry(self, project_id: str, run_id: str, request: RunAction) -> Run:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            self.workspace._current(run.revision, request.expected_revision)
            if run.status not in ("failed", "stopped", "waiting_for_input"):
                raise ApplicationError(
                    "not_retryable",
                    "Stop/reconcile the attempt or use its review action first.",
                    409,
                )
            task = self.workspace._task(db, project_id, run.task_id)
            if run.correction and self._correction_count(db, project_id, run.task_id) >= 2:
                raise ApplicationError(
                    "correction_limit",
                    "Two corrections used. Reconcile scope with the coordinator.",
                    409,
                )
            latest = db.execute(
                "SELECT id FROM work_runs WHERE project_id=? AND task_id=? "
                "ORDER BY number DESC LIMIT 1",
                (project_id, run.task_id),
            ).fetchone()[0]
            if latest != run.id or task.archived:
                raise ApplicationError(
                    "run_changed",
                    "Only the latest attempt of an unarchived task can be retried.",
                    409,
                )
            run.feedback = (
                request.note or "Retry the assignment after inspecting the prior failure."
            )
            if run.question_id and run.input_checkpoint:
                run.status = "waiting_for_input"
            self._save(db, run)
            self._task_state(
                db,
                task,
                "up_next",
                "A fresh attempt was explicitly requested; previous files are preserved.",
                request.author,
            )
            return run

    def usage(self, project_id: str, run_id: str, usage: Usage) -> None:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            # Adapter emits cumulative observations for this one-session attempt. Replays replace.
            if run.usage == usage or (
                run.usage.total_tokens is not None
                and (usage.total_tokens or 0) < run.usage.total_tokens
            ):
                return
            run.usage = usage
            self._save(db, run)

    def record_setup(self, project_id: str, run_id: str, checks: list[CheckResult]) -> None:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            run.setup_checks = checks
            self._save(db, run)

    def local(self, run_id: str) -> dict[str, Any]:
        with self.workspace.connection() as db:
            row = db.execute(
                "SELECT data FROM execution_local WHERE run_id=?", (run_id,)
            ).fetchone()
            return json.loads(row[0]) if row else {}

    def save_local(self, run_id: str, metadata: dict[str, Any]) -> None:
        # Process tracking is durable internal evidence, not a project/catalog change.
        with self.workspace.connection(write=True, notify=False) as db:
            db.execute(
                (
                    "INSERT INTO execution_local VALUES (?,?) ON CONFLICT(run_id) DO "
                    "UPDATE SET data=excluded.data"
                ),
                (run_id, json.dumps(metadata)),
            )

    def restart(self) -> None:
        with self.workspace.connection(write=True) as db:
            for row in db.execute(
                "SELECT data FROM runs WHERE status IN ('preparing','running','stopping')"
            ).fetchall():
                run = Run.model_validate_json(row[0])
                run.status, run.problem = (
                    "uncertain",
                    (
                        "Service restarted. Reconcile the owned process before retrying; "
                        "files are preserved."
                    ),
                )
                self._save(db, run)

    def integrated(self, project_id: str, run_id: str, available: bool) -> Run:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            run = self._run(db, project_id, run_id)
            if run.status != "accepted":
                raise ApplicationError(
                    "not_accepted", "Accept the result before checking code availability.", 409
                )
            run.code_available = available
            self._save(db, run)
            return run
