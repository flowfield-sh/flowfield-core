"""One-level task contributions and their local dependency graph."""

import sqlite3
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from flowfield.application import (
    Edit,
    Input,
    TaskRevision,
    Workspace,
    generated_id,
    now,
)
from flowfield.errors import ApplicationError
from flowfield.project_config import Title

SubtaskIdentifier = Annotated[
    str, StringConstraints(pattern=r"^(?:[a-z0-9][a-z0-9_-]{0,63}|[Ss]-[1-9][0-9]*)$")
]


class SubtaskCreate(Input):
    title: Title
    body: str = Field(min_length=1, max_length=200_000)
    completion: Literal["code", "report"]
    dependencies: list[SubtaskIdentifier] = Field(default_factory=list, max_length=100)
    author: Title = "agent"


class SubtaskEdit(Edit):
    title: Title | None = None
    body: str | None = Field(default=None, min_length=1, max_length=200_000)
    completion: Literal["code", "report"] | None = None
    dependencies: list[SubtaskIdentifier] | None = Field(default=None, max_length=100)


class Subtask(Input):
    workspace_id: str
    task_id: str
    id: str
    key: str
    title: str
    body: str
    completion: Literal["code", "report"]
    dependencies: list[str] = Field(default_factory=list)
    revision: int = 1
    created_at: str
    updated_at: str
    updated_by: str
    contribution_run_id: str | None = None
    status: Literal["pending", "running", "blocked", "complete"] = "pending"


class Subtasks:
    def __init__(self, store: Workspace):
        self.store = store

    @staticmethod
    def read(db: sqlite3.Connection, workspace: str, task: str, identity: str) -> Subtask:
        row = db.execute(
            "SELECT data FROM subtasks WHERE workspace_id=? AND task_id=? "
            "AND (id=? OR json_extract(data,'$.key')=? COLLATE NOCASE)",
            (workspace, task, identity, identity),
        ).fetchone()
        if not row:
            raise ApplicationError("subtask_not_found", "Subtask not found in this task.", 404)
        return Subtask.model_validate_json(row[0])

    @staticmethod
    def read_all(db: sqlite3.Connection, workspace: str, task: str) -> list[Subtask]:
        return [
            Subtask.model_validate_json(row[0])
            for row in db.execute(
                "SELECT data FROM subtasks WHERE workspace_id=? AND task_id=? ORDER BY number",
                (workspace, task),
            )
        ]

    def all(self, workspace: str, task: str) -> list[Subtask]:
        with self.store.connection() as db:
            parent = self.store._task(db, workspace, task)
            return self.read_all(db, workspace, parent.id)

    def get(self, workspace: str, task: str, identity: str) -> Subtask:
        with self.store.connection() as db:
            parent = self.store._task(db, workspace, task)
            return self.read(db, workspace, parent.id, identity)

    @staticmethod
    def _editable(db: sqlite3.Connection, parent: TaskRevision) -> None:
        if parent.archived or parent.status == "done":
            raise ApplicationError("task_closed", "Reopen the task before changing its work.", 409)
        # Frozen running attempts must never acquire a changed assignment mid-flight.
        if (
            db.execute(
                "SELECT 1 FROM runs WHERE workspace_id=? AND task_id=? "
                "AND status IN ('preparing','running','stopping','uncertain')",
                (parent.workspace_id, parent.id),
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM task_reviews WHERE workspace_id=? AND task_id=? "
                "AND status IN ('preparing','running','uncertain')",
                (parent.workspace_id, parent.id),
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM result_versions WHERE workspace_id=? AND task_id=? "
                "AND status IN ('preparing','delivering')",
                (parent.workspace_id, parent.id),
            ).fetchone()
        ):
            raise ApplicationError(
                "task_busy", "Finish or stop active work before changing the plan.", 409
            )

    @staticmethod
    def _graph(records: list[Subtask]) -> None:
        graph = {item.id: set(item.dependencies) for item in records}
        if any(identity in edges or not edges <= graph.keys() for identity, edges in graph.items()):
            raise ApplicationError(
                "invalid_subtask_dependency", "Choose another subtask in this task."
            )
        ready = [identity for identity, edges in graph.items() if not edges]
        visited: set[str] = set()
        while ready:
            identity = ready.pop()
            if identity in visited:
                continue
            visited.add(identity)
            for child, edges in graph.items():
                if identity in edges:
                    edges.remove(identity)
                    if not edges:
                        ready.append(child)
        if len(visited) != len(graph):
            raise ApplicationError(
                "dependency_cycle", "These subtasks would create a dependency cycle."
            )

    @staticmethod
    def save(db: sqlite3.Connection, value: Subtask) -> None:
        db.execute(
            "UPDATE subtasks SET data=? WHERE workspace_id=? AND task_id=? AND id=?",
            (value.model_dump_json(), value.workspace_id, value.task_id, value.id),
        )
        db.execute(
            "INSERT INTO subtask_revisions VALUES (?,?,?,?,?)",
            (value.workspace_id, value.task_id, value.id, value.revision, value.model_dump_json()),
        )
        db.execute(
            "DELETE FROM subtask_dependencies WHERE workspace_id=? AND task_id=? AND subtask_id=?",
            (value.workspace_id, value.task_id, value.id),
        )
        db.executemany(
            "INSERT INTO subtask_dependencies VALUES (?,?,?,?)",
            [
                (value.workspace_id, value.task_id, value.id, dependency)
                for dependency in value.dependencies
            ],
        )

    def _changed(self, db: sqlite3.Connection, parent: TaskRevision, author: str) -> None:
        value = TaskRevision.model_validate(parent.model_dump()).model_copy(
            update={
                "revision": parent.revision + 1,
                "agreement_revision": parent.agreement_revision + 1,
                "updated_at": now(),
                "updated_by": author,
                "change_note": "Updated the task's work plan.",
                "reconciliation_reason": "Work plan changed. Prepare the remaining work again."
                if parent.status not in ("backlog", "up_next")
                else None,
            }
        )
        self.store._apply_task(db, value)

    @staticmethod
    def request_changes(
        db: sqlite3.Connection, parent: TaskRevision, identity: str | None, note: str
    ) -> None:
        from flowfield.execution_models import Run

        records = Subtasks.read_all(db, parent.workspace_id, parent.id)
        affected = (
            {Subtasks.read(db, parent.workspace_id, parent.id, identity).id}
            if identity
            else {item.id for item in records}
        )
        roots = set(affected)
        while True:
            expanded = affected | {
                item.id for item in records if affected.intersection(item.dependencies)
            }
            if expanded == affected:
                break
            affected = expanded
        for item in records:
            if item.id not in affected:
                continue
            if item.contribution_run_id:
                row = db.execute(
                    "SELECT data FROM runs WHERE id=?", (item.contribution_run_id,)
                ).fetchone()
                run = Run.model_validate_json(row[0])
                run.feedback = (
                    note
                    if item.id in roots
                    else "A prerequisite changed. Rebuild this contribution."
                )
                run.status = "changes_requested"
                run.revision += 1
                db.execute(
                    "UPDATE runs SET status=?,data=? WHERE id=?",
                    (run.status, run.model_dump_json(), run.id),
                )
            changed = item.model_copy(
                update={
                    "status": "pending",
                    "contribution_run_id": None,
                    "revision": item.revision + 1,
                    "updated_at": now(),
                }
            )
            Subtasks.save(db, changed)

    def create(self, workspace: str, task: str, request: SubtaskCreate) -> Subtask:
        with self.store.connection(write=True, workspace_id=workspace) as db:
            parent = self.store._task(db, workspace, task)
            self._editable(db, parent)
            records = self.read_all(db, workspace, parent.id)
            if len(records) >= 100:
                raise ApplicationError("subtask_limit", "Use at most 100 subtasks per task.")
            if request.completion == "code" and not parent.project_id:
                raise ApplicationError(
                    "repository_required", "Code contributions require a project."
                )
            dependencies = sorted(
                {self.read(db, workspace, parent.id, value).id for value in request.dependencies}
            )
            number = db.execute(
                "SELECT coalesce(max(number),0)+1 FROM subtasks WHERE workspace_id=? AND task_id=?",
                (workspace, parent.id),
            ).fetchone()[0]
            value = Subtask(
                workspace_id=workspace,
                task_id=parent.id,
                id=generated_id(request.title),
                key=f"S-{number}",
                title=request.title,
                body=request.body,
                completion=request.completion,
                dependencies=dependencies,
                created_at=now(),
                updated_at=now(),
                updated_by=request.author,
            )
            self._graph([*records, value])
            db.execute(
                "INSERT INTO subtasks VALUES (?,?,?,?,?)",
                (workspace, parent.id, value.id, number, value.model_dump_json()),
            )
            self.save(db, value)
            self._changed(db, parent, request.author)
            return value

    def edit(self, workspace: str, task: str, identity: str, request: SubtaskEdit) -> Subtask:
        with self.store.connection(write=True, workspace_id=workspace) as db:
            parent = self.store._task(db, workspace, task)
            self._editable(db, parent)
            current = self.read(db, workspace, parent.id, identity)
            self.store._current(current.revision, request.expected_revision)
            changes = request.model_dump(
                exclude_unset=True, exclude_none=True, exclude={"expected_revision", "author"}
            )
            if "dependencies" in changes:
                changes["dependencies"] = sorted(
                    {
                        self.read(db, workspace, parent.id, value).id
                        for value in changes["dependencies"]
                    }
                )
            value = current.model_copy(update=changes)
            if value.completion == "code" and not parent.project_id:
                raise ApplicationError(
                    "repository_required", "Code contributions require a project."
                )
            if value == current:
                return current
            records = self.read_all(db, workspace, parent.id)
            self._graph([value if item.id == current.id else item for item in records])
            affected = {value.id}
            while True:
                expanded = affected | {
                    item.id for item in records if affected.intersection(item.dependencies)
                }
                if expanded == affected:
                    break
                affected = expanded
            for item in records:
                if item.id not in affected:
                    continue
                changed = value if item.id == value.id else item
                changed = changed.model_copy(
                    update={
                        "revision": changed.revision + 1,
                        "updated_at": now(),
                        "updated_by": request.author,
                        "contribution_run_id": None,
                        "status": "pending",
                    }
                )
                self.save(db, changed)
                if item.id == value.id:
                    value = changed
            self._changed(db, parent, request.author)
            return value
