"""Frozen task inputs and service-owned assembly of local contributions."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import git
from flowfield.application import Task, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record, Run, WorkerResult
from flowfield.subtasks import Subtask, Subtasks


class Assembly(Record):
    id: str
    workspace_id: str
    task_id: str
    project_id: str | None
    signature: str
    prerequisites: list[dict[str, Any]]
    base_commit: str
    branch: str | None = None
    head: str
    pending_head: str | None = None
    created_at: str
    completed_run_id: str | None = None
    contribution_ids: list[str] = Field(default_factory=list)


class TaskAssembly:
    def __init__(self, store: Workspace):
        self.store = store

    @staticmethod
    def read(db: sqlite3.Connection, identity: str) -> Assembly:
        row = db.execute("SELECT data FROM task_assemblies WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ApplicationError(
                "assembly_missing", "Task execution inputs are unavailable.", 409
            )
        return Assembly.model_validate_json(row[0])

    @staticmethod
    def save(db: sqlite3.Connection, value: Assembly) -> None:
        db.execute(
            "UPDATE task_assemblies SET data=? WHERE id=?", (value.model_dump_json(), value.id)
        )

    @staticmethod
    def signature(task: Task, prerequisites: list[dict[str, Any]]) -> str:
        assert task.publication
        # Local plan changes do not invalidate independent contributions. Parent intent,
        # selected sources and exact external results do; agreement revisions still bind review.
        return hashlib.sha256(
            json.dumps(
                {
                    "body": task.body,
                    "criteria": task.acceptance_criteria,
                    "project": task.project_id,
                    "completion": task.publication.completion,
                    "documents": [item.model_dump() for item in task.publication.documents],
                    "files": task.input_files,
                    "prerequisites": prerequisites,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def ensure(self, task: Task, base: str, prerequisites: list[dict[str, Any]]) -> Assembly:
        signature = self.signature(task, prerequisites)
        with self.store.connection(write=True, workspace_id=task.workspace_id) as db:
            current = self.store._task(db, task.workspace_id, task.id)
            self.store._current(current.revision, task.revision)
            row = db.execute(
                "SELECT data FROM task_assemblies WHERE workspace_id=? AND task_id=? "
                "AND signature=?",
                (task.workspace_id, task.id, signature),
            ).fetchone()
            if row:
                value = Assembly.model_validate_json(row[0])
            else:
                if db.execute(
                    "SELECT 1 FROM runs WHERE workspace_id=? AND task_id=? "
                    "AND status IN ('preparing','running','stopping','uncertain')",
                    (task.workspace_id, task.id),
                ).fetchone():
                    raise ApplicationError(
                        "task_busy", "Earlier task inputs still own active work.", 409
                    )
                identity = uuid4().hex
                value = Assembly(
                    id=identity,
                    workspace_id=task.workspace_id,
                    task_id=task.id,
                    project_id=task.project_id,
                    signature=signature,
                    prerequisites=prerequisites,
                    base_commit=base,
                    head=base,
                    created_at=now(),
                    branch=f"flowfield/task-{identity}" if task.project_id else None,
                )
                db.execute(
                    "INSERT INTO "
                    "task_assemblies(id,workspace_id,task_id,signature,data) VALUES (?,?,?,?,?)",
                    (value.id, task.workspace_id, task.id, signature, value.model_dump_json()),
                )
            for child in Subtasks.read_all(db, task.workspace_id, task.id):
                if not child.contribution_run_id:
                    continue
                run = Run.model_validate_json(
                    db.execute(
                        "SELECT data FROM runs WHERE id=?", (child.contribution_run_id,)
                    ).fetchone()[0]
                )
                if run.assembly_id != value.id:
                    child = child.model_copy(
                        update={
                            "revision": child.revision + 1,
                            "contribution_run_id": None,
                            "status": "pending",
                            "updated_at": now(),
                        }
                    )
                    Subtasks.save(db, child)
        if value.project_id:
            self._branch(value)
        return value

    def _branch(self, value: Assembly) -> None:
        assert value.project_id and value.branch
        repository = Path(self.store.project(value.project_id).path)
        marker = f"refs/flowfield/tasks/{value.id}/owner"
        branch = f"refs/heads/{value.branch}"
        with gitops.lock(repository):
            owned = (
                git(
                    repository,
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    marker,
                    allowed_returncodes=(0, 1),
                )
                .decode()
                .strip()
            )
            existing = (
                git(
                    repository,
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    branch,
                    allowed_returncodes=(0, 1),
                )
                .decode()
                .strip()
            )
            if not owned and not existing:
                # Each ref is compare-and-set. The marker makes an interrupted creation
                # distinguishable from a colliding, human-owned branch.
                git(
                    repository,
                    "update-ref",
                    marker,
                    value.base_commit,
                    "0" * len(value.base_commit),
                )
                owned = value.base_commit
            if owned != value.base_commit:
                raise ApplicationError(
                    "assembly_ownership_changed",
                    "Task branch ownership changed. Inspect the repository.",
                    409,
                )
            if not existing:
                git(
                    repository,
                    "update-ref",
                    branch,
                    value.base_commit,
                    "0" * len(value.base_commit),
                )
                existing = value.base_commit
            if (
                f"branch {branch}"
                in git(repository, "worktree", "list", "--porcelain").decode().splitlines()
            ):
                raise ApplicationError(
                    "assembly_checked_out",
                    "The task assembly branch is checked out. Leave that branch before continuing.",
                    409,
                )
            if value.pending_head:
                if existing == value.head:
                    git(repository, "update-ref", branch, value.pending_head, value.head)
                elif existing != value.pending_head:
                    raise ApplicationError(
                        "assembly_ownership_changed", "Task branch moved unexpectedly.", 409
                    )
                with self.store.connection(write=True, workspace_id=value.workspace_id) as db:
                    current = self.read(db, value.id)
                    current.head, current.pending_head = value.pending_head, None
                    self.save(db, current)
                value.head, value.pending_head = value.pending_head, None
            elif existing != value.head:
                raise ApplicationError(
                    "assembly_ownership_changed", "Task branch moved unexpectedly.", 409
                )

    def baseline(self, value: Assembly, child: Subtask) -> tuple[str, list[Run]]:
        from flowfield.execution import Execution

        with self.store.connection() as db:
            siblings = {
                item.id: item for item in Subtasks.read_all(db, value.workspace_id, value.task_id)
            }
            dependencies: set[str] = set()
            pending = list(child.dependencies)
            while pending:
                identity = pending.pop()
                if identity in dependencies:
                    continue
                dependencies.add(identity)
                pending.extend(siblings[identity].dependencies)
            runs = []
            for identity in sorted(dependencies):
                sibling = siblings[identity]
                if not sibling.contribution_run_id:
                    raise ApplicationError(
                        "subtask_blocked", "A prerequisite contribution is unfinished.", 409
                    )
                run = Execution(self.store)._run(
                    db, value.workspace_id, sibling.contribution_run_id
                )
                if run.assembly_id != value.id or run.status != "contributed":
                    raise ApplicationError(
                        "subtask_blocked", "A prerequisite contribution needs fresh inputs.", 409
                    )
                runs.append(run)
            previous = db.execute(
                "SELECT data FROM runs WHERE workspace_id=? AND task_id=? AND subtask_id=? "
                "ORDER BY number DESC LIMIT 1",
                (value.workspace_id, value.task_id, child.id),
            ).fetchone()
            prior = Run.model_validate_json(previous[0]) if previous else None
        # Corrections, retries and local plan edits retain work with current dependencies.
        # Only dependency runs become prerequisite evidence; the prior attempt remains
        # the predecessor. Never reuse code from a different frozen task assembly.
        corrected = []
        if prior and prior.assembly_id == value.id and prior.completion == child.completion:
            commit = (
                prior.checkpoint_commit
                if prior.status in ("failed", "stopped")
                else prior.result_commit
                if prior.status in ("contributed", "changes_requested")
                else None
            )
            if commit:
                corrected.append(prior.model_copy(update={"result_commit": commit}))
        return self._combine(value, [*runs, *corrected]), runs

    def _combine(self, value: Assembly, runs: list[Run]) -> str:
        if not value.project_id:
            return ""
        repository = Path(self.store.project(value.project_id).path)
        head = value.base_commit
        with gitops.lock(repository):
            for run in runs:
                if (
                    run.completion == "report"
                    and run.result_commit
                    and git(repository, "rev-parse", run.base_commit + "^{tree}")
                    != git(repository, "rev-parse", run.result_commit + "^{tree}")
                ):
                    raise ApplicationError(
                        "report_changes_code",
                        "A report contribution changes code. Reconcile its "
                        "completion requirement before assembling the task.",
                        409,
                    )
                if run.completion == "code" and run.result_commit:
                    identity = hashlib.sha256(
                        f"{value.id}:{head}:{run.result_commit}".encode()
                    ).hexdigest()
                    head = gitops.candidate(repository, head, run.result_commit, identity)
        return head

    def assemble(self, workspace: str, task_id: str) -> bool:
        """Publish one whole-task result after every exact contribution is retained."""
        from flowfield.execution import Execution
        from flowfield.outputs import Outputs
        from flowfield.results import Results

        with self.store.connection() as db:
            task = self.store._task(db, workspace, task_id)
            children = Subtasks.read_all(db, workspace, task.id)
            if not children or any(not item.contribution_run_id for item in children):
                return False
            if (
                task.archived
                or task.status not in ("up_next", "in_progress")
                or task.readiness != "ready"
            ):
                return False
            runs = [
                Execution(self.store)._run(db, workspace, item.contribution_run_id or "")
                for item in children
            ]
            if any(
                run.status != "contributed" or run.assembly_id != runs[0].assembly_id
                for run in runs
            ):
                return False
            assert runs[0].assembly_id and task.publication
            value = self.read(db, runs[0].assembly_id)
            if self.signature(task, value.prerequisites) != value.signature:
                return False
            ids = [run.id for run in runs]
            if value.contribution_ids == ids and value.completed_run_id:
                prior = Execution(self.store)._run(db, workspace, value.completed_run_id)
                if prior.agreement_revision == task.agreement_revision:
                    return False
            files = [file for run in runs for file in Outputs.for_run(db, run.id)]
            from flowfield.outputs import MAX_SUBMISSION_BYTES

            if len(files) > 256 or sum(file.size for file in files) > MAX_SUBMISSION_BYTES:
                raise ApplicationError(
                    "task_outputs_limit",
                    "Keep the combined task output within 256 files and 512 MiB. "
                    "Revise contributions to retain only their deliverables.",
                    409,
                )
            output_ids = [file.id for file in files]
        head = self._combine(value, runs)
        if value.project_id:
            with self.store.connection(write=True, workspace_id=workspace) as db:
                value = self.read(db, value.id)
                value.pending_head = head
                self.save(db, value)
            self._branch(value)
        with self.store.connection(write=True, workspace_id=workspace) as db:
            current = self.store._task(db, workspace, task.id)
            self.store._current(current.revision, task.revision)
            latest = Subtasks.read_all(db, workspace, task.id)
            if [item.contribution_run_id for item in latest] != ids:
                raise ApplicationError(
                    "assembly_changed", "Contributions changed during assembly.", 409
                )
            value = self.read(db, value.id)
            if value.contribution_ids == ids and value.completed_run_id:
                prior = Execution(self.store)._run(db, workspace, value.completed_run_id)
                if prior.agreement_revision == current.agreement_revision:
                    return False
            run = Run(
                id=uuid4().hex,
                workspace_id=workspace,
                project_id=task.project_id,
                task_id=task.id,
                task_key=task.key,
                purpose="assembly",
                assembly_id=value.id,
                status="in_review",
                environment_id=uuid4().hex,
                model="",
                agreement_revision=task.agreement_revision,
                base_commit=value.base_commit,
                result_commit=head or None,
                completion=task.publication.completion,
                target_branch=task.publication.target_branch,
                created_at=now(),
                ended_at=now(),
                documents=task.publication.documents,
                result=WorkerResult(
                    summary="\n\n".join(worker.result.summary for worker in runs if worker.result)[
                        :8000
                    ],
                    checks="\n\n".join(
                        f"### {child.title}\n\n{worker.result.checks}"
                        for child, worker in zip(children, runs, strict=True)
                        if worker.result
                    )[:8000],
                    limitations="\n\n".join(
                        worker.result.limitations
                        for worker in runs
                        if worker.result and worker.result.limitations
                    )[:8000],
                ),
            )
            assignment = Execution(self.store)._assignment(
                db, task, run, None, value.prerequisites, {}, resume=False
            )
            assignment["contributions"] = json.dumps(
                [
                    {
                        "subtask": child.model_dump(),
                        "run_id": worker.id,
                        "commit": worker.result_commit,
                    }
                    for child, worker in zip(children, runs, strict=True)
                ]
            )
            db.execute(
                "INSERT INTO runs(id,workspace_id,task_id,status,data,assignment) "
                "VALUES (?,?,?,?,?,?)",
                (
                    run.id,
                    workspace,
                    task.id,
                    run.status,
                    run.model_dump_json(),
                    json.dumps(assignment),
                ),
            )
            Results(self.store).capture(db, run, output_ids=output_ids)
            value.completed_run_id, value.contribution_ids = run.id, ids
            self.save(db, value)
            Execution(self.store)._task_state(
                db, task, "in_progress", "All contributions assembled for task review."
            )
            return True
