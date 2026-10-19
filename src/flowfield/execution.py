"""Transactional queue, attempt ownership and review. No harness or OS operations."""

import json
import sqlite3
from typing import Any
from uuid import uuid4

from flowfield import worker_settings
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
    WorkspaceWorkerSettings,
)
from flowfield.execution_readiness import TaskAgent, task_agents
from flowfield.harness_models import HarnessLaunch
from flowfield.questions import Question, QuestionCreate, Questions


class Execution:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _settings(self, db: sqlite3.Connection, workspace_id: str) -> WorkspaceWorkerSettings:
        self.workspace._workspace(db, workspace_id)
        return worker_settings.read_workspace(db, workspace_id)

    def settings(self, workspace_id: str) -> WorkspaceWorkerSettings:
        with self.workspace.connection() as db:
            return self._settings(db, workspace_id)

    def occupancy(self, workspace_id: str) -> WorkerOccupancy:
        with self.workspace.connection() as db:
            self.workspace._workspace(db, workspace_id)
            return worker_settings.occupancy(db, workspace=workspace_id)

    def _save_settings(self, db: sqlite3.Connection, settings: WorkspaceWorkerSettings) -> None:
        worker_settings.save_workspace(db, settings)

    def configure(self, workspace_id: str, request: SettingsEdit) -> WorkspaceWorkerSettings:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            return self._configure(db, workspace_id, request)

    def _configure(
        self, db: sqlite3.Connection, workspace_id: str, request: SettingsEdit
    ) -> WorkspaceWorkerSettings:
        settings = self._settings(db, workspace_id)
        self.workspace._current(settings.revision, request.expected_revision)
        worker_settings.configure(settings, request)
        settings.problem = None
        self._save_settings(db, settings)
        return settings

    def queue(self, workspace_id: str, request: QueueEdit) -> WorkspaceWorkerSettings:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            settings = self._settings(db, workspace_id)
            self.workspace._current(settings.revision, request.expected_revision)
            worker_settings.queue(settings, request)
            settings.problem = None
            self._save_settings(db, settings)
            return settings

    def queue_problem(self, workspace_id: str, problem: str | None) -> None:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            settings = self._settings(db, workspace_id)
            if settings.problem == problem:
                return
            settings.problem = problem
            self._save_settings(db, settings)

    def task_problem(self, workspace_id: str, task_id: str, problem: str | None) -> None:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            self.workspace._task(db, workspace_id, task_id)
            db.execute(
                "UPDATE tasks SET scheduling_problem=? WHERE workspace_id=? AND id=? "
                "AND scheduling_problem IS NOT ?",
                (problem, workspace_id, task_id, problem),
            )

    def _run(self, db: sqlite3.Connection, workspace_id: str, run_id: str) -> Run:
        row = db.execute(
            "SELECT data FROM runs WHERE workspace_id=? AND id=?", (workspace_id, run_id)
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Attempt not found in this project.", 404)
        return Run.model_validate_json(row[0])

    def get(self, workspace_id: str, run_id: str) -> Run:
        with self.workspace.connection() as db:
            return self._run(db, workspace_id, run_id)

    def _save(self, db: sqlite3.Connection, run: Run) -> None:
        run.revision += 1
        db.execute(
            "UPDATE runs SET status=?, data=? WHERE id=?",
            (run.status, run.model_dump_json(), run.id),
        )

    def _correction_count(self, db: sqlite3.Connection, workspace_id: str, task_id: str) -> int:
        # Input continuations retain their correction number; explicit retries consume another.
        return int(
            db.execute(
                "SELECT coalesce(max(json_extract(data,'$.correction.number')),0) "
                "FROM runs WHERE workspace_id=? AND task_id=?",
                (workspace_id, task_id),
            ).fetchone()[0]
        )

    def page(
        self,
        workspace_id: str,
        *,
        task_id: str | None = None,
        before: int | None = None,
        limit: int = 20,
        attention: bool = False,
    ) -> RunPage:
        limit = max(1, min(limit, 50))
        with self.workspace.connection() as db:
            self.workspace._workspace(db, workspace_id)
            if task_id:
                task_id = self.workspace._task(db, workspace_id, task_id).id
            rows = db.execute(
                (
                    "SELECT number, data FROM runs r WHERE workspace_id=? AND (? IS NULL"
                    " OR task_id=?) AND (? IS NULL OR number<?) "
                    "AND (?=0 OR (status IN ('in_review','failed','uncertain') AND NOT"
                    " EXISTS (SELECT 1 FROM runs n WHERE n.workspace_id=r.workspace_id AND"
                    " n.task_id=r.task_id AND n.subtask_id IS r.subtask_id "
                    "AND n.number>r.number))) ORDER BY number "
                    "DESC LIMIT ?"
                ),
                (workspace_id, task_id, task_id, before, before, int(attention), limit + 1),
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

    def _current_assignment(self, db: sqlite3.Connection, run: Run, task: Task) -> None:
        from flowfield.input_delivery import other_input_blocks
        from flowfield.subtasks import Subtasks

        child = (
            Subtasks.read(db, run.workspace_id, task.id, run.subtask_id) if run.subtask_id else None
        )
        blocked = (
            (
                bool(task.blocked_by or task.reconciliation_reason)
                or other_input_blocks(db, run.workspace_id, task.id, "", run.subtask_id)
            )
            if child
            else task.readiness != "ready"
        )
        if (
            task.archived
            or blocked
            or task.agreement_revision != run.agreement_revision
            or not task.publication
            or task.publication_status != "published"
            or (child.completion if child else task.publication.completion) != run.completion
            or task.publication.target_branch != run.target_branch
        ):
            raise ApplicationError(
                "assignment_changed",
                "This result belongs to an older or blocked assignment. Reconcile "
                "the task and request a revised attempt before accepting.",
                409,
            )

    def scheduling_candidates(self) -> list[tuple[str, str]]:
        """Metadata ordering only; claim rechecks eligibility and shared capacity atomically."""
        with self.workspace.connection() as db:
            return [
                (row[0], row[1])
                for row in db.execute(
                    "SELECT workspace_id,id FROM tasks WHERE json_extract(data,'$.archived')=0 "
                    "AND json_extract(data,'$.status') IN ('up_next','in_progress') "
                    "ORDER BY position,key"
                )
            ]

    def _prerequisites(
        self, db: sqlite3.Connection, task: Task, available_results: set[str]
    ) -> list[dict[str, Any]] | None:
        """Resolve accepted prerequisite evidence inside the claim transaction."""
        prerequisites: list[dict[str, Any]] = []
        usable = True
        for reference in task.prerequisites:
            dependency = reference.id
            dependency_project = reference.workspace_id
            row = db.execute(
                (
                    "SELECT r.data FROM runs r JOIN result_versions v ON v.run_id=r.id "
                    "WHERE r.workspace_id=? AND r.task_id=? AND v.status='delivered' "
                    "ORDER BY v.version DESC LIMIT 1"
                ),
                (dependency_project, dependency),
            ).fetchone()
            if row:
                prerequisite = Run.model_validate_json(row[0])
                if (
                    reference.project_id == task.project_id
                    and task.project_id is not None
                    and prerequisite.completion == "code"
                    and prerequisite.result_commit not in available_results
                ):
                    usable = False
                prerequisites.append(
                    {
                        "task": prerequisite.task_key,
                        "workspace_id": dependency_project,
                        "project_id": reference.project_id,
                        "repository_scope": "same_repository"
                        if reference.project_id == task.project_id and task.project_id is not None
                        else "other_repository",
                        "run_id": prerequisite.id,
                        "completion": prerequisite.completion,
                        "agreement_revision": prerequisite.agreement_revision,
                        "commit": prerequisite.result_commit
                        if prerequisite.completion == "code"
                        else None,
                        "result": prerequisite.result.model_dump() if prerequisite.result else None,
                    }
                )
            else:
                usable = False
        return prerequisites if usable else None

    def _assignment(
        self,
        db: sqlite3.Connection,
        task: Task,
        run: Run,
        predecessor: Run | None,
        prerequisites: list[dict[str, Any]],
        runtime_settings: dict[str, Any],
        *,
        resume: bool,
    ) -> dict[str, str]:
        """Freeze context once; input continuations reuse their original assignment."""
        workspace_id = task.workspace_id
        assert task.publication
        from flowfield.worker_checks import CheckPermissions

        runtime_settings["worker_checks_enabled"] = (
            CheckPermissions(self.workspace).read(db, task.project_id).enabled
            if task.project_id
            else False
        )
        if resume:
            assert predecessor
            run.documents = predecessor.documents
            assignment: dict[str, str] = json.loads(
                db.execute("SELECT assignment FROM runs WHERE id=?", (predecessor.id,)).fetchone()[
                    0
                ]
            )
            assignment.update(
                validation=json.dumps(runtime_settings),
                feedback=run.feedback,
                correction=run.correction.model_dump_json() if run.correction else "",
            )
            return assignment
        project = self.workspace._project(db, task.project_id) if task.project_id else None
        assignment = {
            "agreement": json.dumps(
                {
                    "task": task.key,
                    "task_revision": task.revision,
                    "agreement_revision": task.agreement_revision,
                    "completion": task.publication.completion,
                    "repository": project is not None,
                    "target_branch": task.publication.target_branch,
                    "base_commit": run.base_commit,
                    "scope": "Frozen at claim; discoveries do not change intent.",
                }
            ),
            "description": task.body,
            "project": project.description if project else "",
            "criteria": task.acceptance_criteria,
            "prerequisites": json.dumps(prerequisites),
            "feedback": run.feedback,
            "predecessor": predecessor.result.model_dump_json()
            if predecessor and predecessor.result
            else "",
            "title": f"{task.key}: {task.title}",
            "correction": run.correction.model_dump_json() if run.correction else "",
            "validation": json.dumps(runtime_settings),
        }
        from flowfield.task_files import freeze

        file_ids = list(task.input_files)
        from flowfield.outputs import Outputs

        if predecessor:
            file_ids.extend(file.id for file in Outputs.for_run(db, predecessor.id))
        for prerequisite in prerequisites:
            if prerequisite.get("run_id"):
                file_ids.extend(file.id for file in Outputs.for_run(db, prerequisite["run_id"]))
        assignment["files"] = (
            json.dumps(
                [
                    file.model_dump()
                    for file in freeze(
                        db,
                        workspace_id,
                        file_ids,
                    )
                ]
            )
            if file_ids
            else "[]"
        )
        from flowfield.worker_documents import sections as document_sections

        run.documents = task.publication.documents
        assignment.update(document_sections(db, run.documents))
        handoff = db.execute(
            "SELECT body FROM activity WHERE workspace_id=? AND task_id=? "
            "AND kind='handoff' ORDER BY sequence DESC LIMIT 1",
            (workspace_id, task.id),
        ).fetchone()
        assignment["handoff"] = handoff[0] if handoff else ""
        assignment["questions"] = json.dumps(
            [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM questions WHERE workspace_id=? AND task_id=? "
                    "AND status IN ('open','answered') ORDER BY number",
                    (workspace_id, task.id),
                )
            ]
        )
        if predecessor and predecessor.agreement_revision == run.agreement_revision:
            previous_sections = json.loads(
                db.execute("SELECT assignment FROM runs WHERE id=?", (predecessor.id,)).fetchone()[
                    0
                ]
            )
            for section in ("input", "exchange_history", "earlier_answers"):
                if section in previous_sections:
                    assignment[section] = previous_sections[section]
        return assignment

    def claim(
        self,
        workspace_id: str,
        baseline: str,
        available_results: dict[str, set[str]],
        *,
        task_id: str | None = None,
        subtask_id: str | None = None,
        assembly_id: str | None = None,
        contributions: list[Run] | None = None,
        checked_agents: dict[str, TaskAgent] | None = None,
    ) -> Run | None:
        """Reserve once against fresh state; caller supplies verified immutable Git facts."""
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            settings = self._settings(db, workspace_id)
            occupied = worker_settings.occupancy(db, workspace=workspace_id)
            count = occupied.active + occupied.uncertain
            if not settings.enabled or not settings.selection or count >= settings.max_parallel:
                return None
            waiting_for_code = []
            candidates = (
                [self.workspace._task(db, workspace_id, task_id)]
                if task_id
                else self.workspace._tasks(db, workspace_id, False)
            )
            from flowfield.subtasks import Subtasks
            from flowfield.task_assembly import TaskAssembly

            for task in candidates:
                current_agents = task_agents(self.workspace, db, workspace_id, task.id)
                if checked_agents is not None and checked_agents != current_agents:
                    continue
                children = Subtasks.read_all(db, workspace_id, task.id)
                child = Subtasks.read(db, workspace_id, task.id, subtask_id) if subtask_id else None
                if bool(children) != bool(child):
                    continue
                if child:
                    if child.status not in ("pending", "blocked") or child.contribution_run_id:
                        continue
                    if any(
                        not sibling.contribution_run_id
                        for sibling in children
                        if sibling.id in child.dependencies
                    ):
                        continue
                    assert assembly_id
                    assembly = TaskAssembly.read(db, assembly_id)
                    if assembly.workspace_id != workspace_id or assembly.task_id != task.id:
                        raise ApplicationError(
                            "assembly_owner", "Task inputs belong to another task."
                        )
                if task.archived:
                    continue
                previous = db.execute(
                    "SELECT data FROM runs WHERE workspace_id=? AND task_id=? AND subtask_id IS ? "
                    "ORDER BY number DESC LIMIT 1",
                    (workspace_id, task.id, subtask_id),
                ).fetchone()
                predecessor = Run.model_validate_json(previous[0]) if previous else None
                questions = Questions(self.workspace)
                pending_input = (
                    questions._get(db, workspace_id, predecessor.question_id)
                    if predecessor
                    and predecessor.status == "waiting_for_input"
                    and predecessor.question_id
                    and predecessor.input_checkpoint
                    else None
                )
                from flowfield.input_delivery import other_input_blocks

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
                    and not other_input_blocks(
                        db, workspace_id, task.id, pending_input.id, subtask_id
                    )
                )
                if (
                    not continuation
                    and (
                        task.status
                        not in (
                            ("up_next", "in_progress")
                            if child and child.status == "pending"
                            else ("up_next",)
                        )
                        or task.readiness != "ready"
                    )
                ) or task.publication_status != "published":
                    continue
                if db.execute(
                    (
                        "SELECT 1 FROM runs WHERE workspace_id=? AND task_id=? AND "
                        "subtask_id IS ? AND status IN"
                        " ('preparing','running','stopping','uncertain')"
                    ),
                    (workspace_id, task.id, subtask_id),
                ).fetchone():
                    continue
                correction = predecessor.next_correction if predecessor else None
                if predecessor and predecessor.status in ("failed", "stopped", "waiting_for_input"):
                    correction = predecessor.correction
                if correction and not continuation:
                    correction_count = self._correction_count(db, workspace_id, task.id)
                    correction = correction.model_copy(update={"number": correction_count + 1})
                chosen_base = (
                    correction.base_commit
                    if correction
                    else predecessor.result_commit
                    if predecessor
                    and predecessor.status == "changes_requested"
                    and predecessor.result_commit
                    and not child
                    else baseline
                )
                retrying_input = bool(
                    predecessor
                    and predecessor.input_question_id
                    and predecessor.status in ("failed", "stopped")
                    and task.agreement_revision == predecessor.agreement_revision
                    and task.publication
                    and (child.completion if child else task.publication.completion)
                    == predecessor.completion
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
                if (
                    predecessor
                    and predecessor.checkpoint_commit
                    and predecessor.agreement_revision == task.agreement_revision
                    and predecessor.status in ("failed", "stopped")
                    and task.publication
                    and task.publication.target_branch == predecessor.target_branch
                    and (child.completion if child else task.publication.completion)
                    == predecessor.completion
                ):
                    chosen_base = predecessor.base_commit
                    input_base = predecessor.checkpoint_commit
                prerequisites = self._prerequisites(
                    db, task, available_results.get(input_base or chosen_base, set())
                )
                if prerequisites is None:
                    waiting_for_code.append(task.key)
                    continue
                if child:
                    if (
                        prerequisites != assembly.prerequisites
                        or TaskAssembly.signature(task, prerequisites) != assembly.signature
                    ):
                        raise ApplicationError(
                            "task_inputs_changed", "Task prerequisites changed before claim.", 409
                        )
                    required = set(child.dependencies)
                    pending = list(required)
                    while pending:
                        sibling = Subtasks.read(db, workspace_id, task.id, pending.pop())
                        for dependency in sibling.dependencies:
                            if dependency not in required:
                                required.add(dependency)
                                pending.append(dependency)
                    if {item.subtask_id for item in contributions or []} != required:
                        raise ApplicationError(
                            "subtask_inputs_changed",
                            "Freeze every prerequisite contribution before claiming work.",
                            409,
                        )
                    for contribution in contributions or []:
                        sibling = Subtasks.read(
                            db, workspace_id, task.id, contribution.subtask_id or ""
                        )
                        if (
                            sibling.contribution_run_id != contribution.id
                            or contribution.assembly_id != assembly_id
                        ):
                            raise ApplicationError(
                                "subtask_inputs_changed",
                                "A prerequisite contribution changed.",
                                409,
                            )
                        prerequisites.append(
                            {
                                "workspace_id": workspace_id,
                                "project_id": task.project_id,
                                "task_id": task.id,
                                "subtask_id": sibling.id,
                                "run_id": contribution.id,
                                "commit": contribution.result_commit,
                                "summary": contribution.result.summary
                                if contribution.result
                                else "",
                            }
                        )
                assert task.publication
                runtime_row = db.execute(
                    "SELECT data FROM integration_settings WHERE project_id=?", (task.project_id,)
                ).fetchone()
                runtime_settings = json.loads(runtime_row[0]) if runtime_row else {}
                if (
                    task.publication.target_branch
                    and task.publication.target_branch != runtime_settings["target_branch"]
                ):
                    continue
                if (
                    (continuation or retrying_input)
                    and predecessor
                    and (
                        (child.completion if child else task.publication.completion)
                        != predecessor.completion
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
                    .resolve(db, workspace_id, "worker", task.id)
                    .effective
                )
                assert effective
                effective = AgentSettings(self.workspace).freeze(db, effective)
                run = Run(
                    id=uuid4().hex,
                    workspace_id=workspace_id,
                    project_id=task.project_id,
                    task_id=task.id,
                    subtask_id=child.id if child else None,
                    subtask_revision=child.revision if child else None,
                    assembly_id=assembly_id,
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
                    check_settings_revision=runtime_settings.get("revision", 1),
                    project_checks=runtime_settings.get("checks", []),
                    check_timeout_seconds=runtime_settings.get("check_timeout_seconds", 60),
                    setup_commands=runtime_settings.get("setup_commands", []),
                    setup_timeout_seconds=runtime_settings.get("setup_timeout_seconds", 120),
                    model=effective.choice.model,
                    effort=effective.choice.effort,
                    agent_settings=effective,
                    agreement_revision=task.agreement_revision,
                    base_commit=chosen_base,
                    completion=child.completion if child else task.publication.completion,
                    target_branch=task.publication.target_branch,
                    feedback=predecessor.feedback
                    if predecessor and predecessor.status != "accepted"
                    else "",
                    created_at=now(),
                )
                assignment = self._assignment(
                    db,
                    task,
                    run,
                    predecessor,
                    prerequisites,
                    runtime_settings,
                    resume=continuation or retrying_input,
                )
                if child:
                    assignment["task_intent"] = task.body
                    assignment["description"] = child.body
                    assignment["title"] = f"{task.key} / {child.key}: {child.title}"
                    assignment["contribution"] = child.model_dump_json()
                    assignment["agreement"] = json.dumps(
                        {
                            "agreement_revision": task.agreement_revision,
                            "subtask_revision": child.revision,
                            "completion": child.completion,
                            "scope": (
                                "Submit this contribution. Whole-task review and delivery "
                                "follow assembly."
                            ),
                        }
                    )
                    child = child.model_copy(
                        update={
                            "status": "running",
                            "revision": child.revision + 1,
                            "updated_at": now(),
                        }
                    )
                    Subtasks.save(db, child)
                if continuation:
                    assert pending_input and predecessor
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

                assignment["project_checks"] = (
                    "Use run_project_checks for human-authorized saved commands. "
                    "This section retains the latest output; "
                    "read_context retrieves current pages."
                )
                enrich(db, run, assignment)
                db.execute(
                    (
                        "INSERT INTO "
                        "runs(id,workspace_id,task_id,subtask_id,status,data,assignment) "
                        "VALUES (?,?,?,?,?,?,?)"
                    ),
                    (
                        run.id,
                        workspace_id,
                        task.id,
                        run.subtask_id,
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
            if waiting_for_code:
                raise ApplicationError(
                    "prerequisite_code_unavailable",
                    ", ".join(waiting_for_code[:5])
                    + " waits for accepted prerequisite code in the project baseline. "
                    "Integrate those results before dependent work can start.",
                    409,
                )
            return None

    def assignment(self, workspace_id: str, run_id: str) -> dict[str, str]:
        with self.workspace.connection() as db:
            return self._saved_assignment(db, workspace_id, run_id)

    def _saved_assignment(
        self, db: sqlite3.Connection, workspace_id: str, run_id: str
    ) -> dict[str, str]:
        self._run(db, workspace_id, run_id)
        value: dict[str, str] = json.loads(
            db.execute("SELECT assignment FROM runs WHERE id=?", (run_id,)).fetchone()[0]
        )
        return value

    def worker_access(self, workspace_id: str, run_id: str) -> Run:
        run = self.get(workspace_id, run_id)
        if run.status != "running":
            raise ApplicationError(
                "worker_scope_closed", "This attempt no longer owns a running assignment.", 409
            )
        return run

    def context_section(self, workspace_id: str, run_id: str, section: str) -> str:
        from flowfield.worker_context import attempt_source

        with self.workspace.connection() as db:
            run = self._run(db, workspace_id, run_id)
            sections = json.loads(
                db.execute("SELECT assignment FROM runs WHERE id=?", (run_id,)).fetchone()[0]
            )
            if section == "project_checks":
                return json.dumps([item.model_dump() for item in run.project_check_results])
            if section.startswith("attempt:"):
                return attempt_source(db, run, sections, section.removeprefix("attempt:"))
            if section not in sections:
                raise ApplicationError(
                    "unknown_section", "Available sections: " + ", ".join(sections)
                )
            return str(sections[section])

    def report_result(self, workspace_id: str, run_id: str, result: WorkerResult) -> None:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
            if run.status != "running" or run.result:
                raise ApplicationError("worker_scope_closed", "This report is closed.", 409)
            from flowfield.outputs import Outputs

            Outputs(self.workspace).capture(db, run, result.files)
            run.result = result
            self._save(db, run)

    def excluded_files(self, workspace_id: str, run_id: str, paths: list[str]) -> None:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
            run.excluded_files = paths[:100]
            self._save(db, run)

    def ask_question(self, workspace_id: str, run_id: str, request: QuestionCreate) -> Question:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
            if run.status != "running" or run.result or request.task_id != run.task_id:
                raise ApplicationError("worker_scope_closed", "This assignment is closed.", 409)
            if run.question_id:
                return Questions(self.workspace)._get(db, workspace_id, run.question_id)
            question = Questions(self.workspace)._ask(
                db, workspace_id, request, origin_run_id=run.id
            )
            run.question_id = question.id
            self._save(db, run)
            return question

    def started(
        self,
        workspace_id: str,
        run_id: str,
        *,
        applied_agent: AgentChoice | None = None,
        harness_launch: HarnessLaunch | None = None,
    ) -> Run:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
            if run.status != "preparing":
                raise ApplicationError("run_changed", "Attempt no longer awaits launch.", 409)
            task = self.workspace._task(db, workspace_id, run.task_id)
            self._current_assignment(db, run, task)
            run.applied_agent = applied_agent
            run.harness_launch = harness_launch
            run.status, run.started_at = "running", now()
            self._save(db, run)
            return run

    def finish(
        self,
        workspace_id: str,
        run_id: str,
        status: RunStatus,
        *,
        result: WorkerResult | None = None,
        commit: str | None = None,
        input_checkpoint: str | None = None,
        problem: str | None = None,
    ) -> Run:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
            if run.status not in ACTIVE:
                raise ApplicationError(
                    "run_changed", "A late result cannot replace a finished attempt.", 409
                )
            if run.status == "stopping" and status != "uncertain":
                status, problem = (
                    "stopped",
                    "Stopped before result submission completed. Work preserved.",
                )
            repository = run.project_id
            if status == "in_review" and (not result or (repository and not commit)):
                raise ApplicationError(
                    "result_missing", "Review requires a result and immutable code reference."
                )
            run.status, run.result_commit, run.problem = status, commit, problem
            if status == "waiting_for_input" or input_checkpoint is not None:
                run.input_checkpoint = input_checkpoint
            run.result = result or run.result
            run.ended_at = now() if status != "uncertain" else None
            self._save(db, run)
            task = self.workspace._task(db, workspace_id, run.task_id)
            if run.subtask_id:
                from flowfield.subtasks import Subtasks

                child = Subtasks.read(db, workspace_id, task.id, run.subtask_id)
                complete = status == "in_review" and run.result and run.result.outcome == "complete"
                if status == "in_review" and not complete:
                    run.status, run.problem = (
                        "failed",
                        "This contribution is partial. Continue the remaining work.",
                    )
                    self._save(db, run)
                if complete:
                    run.status = "contributed"
                    self._save(db, run)
                child = child.model_copy(
                    update={
                        "status": "complete" if complete else "blocked",
                        "contribution_run_id": run.id if complete else None,
                        "revision": child.revision + 1,
                        "updated_at": now(),
                    }
                )
                Subtasks.save(db, child)
            elif status == "in_review":
                from flowfield.results import Results

                Results(self.workspace).capture(db, run)
                self._task_state(
                    db,
                    task,
                    "in_progress",
                    f"Attempt {run.id[:8]} submitted its result. "
                    "Preparing the proposed result for review.",
                )
            return run

    def stop_requested(self, workspace_id: str, run_id: str, request: RunAction) -> Run:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
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

    def retry(self, workspace_id: str, run_id: str, request: RunAction) -> Run:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
            self.workspace._current(run.revision, request.expected_revision)
            if run.status not in ("failed", "stopped", "waiting_for_input"):
                raise ApplicationError(
                    "not_retryable",
                    "Stop/reconcile the attempt or use its review action first.",
                    409,
                )
            task = self.workspace._task(db, workspace_id, run.task_id)
            latest = db.execute(
                "SELECT id FROM runs WHERE workspace_id=? AND task_id=? AND subtask_id IS ? "
                "ORDER BY number DESC LIMIT 1",
                (workspace_id, run.task_id, run.subtask_id),
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

    def usage(self, workspace_id: str, run_id: str, usage: Usage) -> None:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
            # Adapter emits cumulative observations for this one-session attempt. Replays replace.
            if run.usage == usage or (
                run.usage.total_tokens is not None
                and (usage.total_tokens or 0) < run.usage.total_tokens
            ):
                return
            run.usage = usage
            self._save(db, run)

    def record_setup(self, workspace_id: str, run_id: str, checks: list[CheckResult]) -> None:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
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

    def integrated(self, workspace_id: str, run_id: str, available: bool) -> Run:
        with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
            run = self._run(db, workspace_id, run_id)
            if run.status != "accepted":
                raise ApplicationError(
                    "not_accepted", "Accept the result before checking code availability.", 409
                )
            run.code_available = available
            self._save(db, run)
            return run
