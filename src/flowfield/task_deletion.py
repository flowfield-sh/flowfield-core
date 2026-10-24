"""Permanent task removal with atomic records and retryable, narrowly owned file cleanup."""

import json
import logging
import re
import shutil
import sqlite3
import time
from contextlib import nullcontext
from pathlib import Path

from pydantic import BaseModel, Field

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import git
from flowfield.application import Edit, TaskRevision, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.storage import acquire_lock


class CleanupPath(BaseModel):
    relative: str
    repository: str | None = None


class TaskDeletion(BaseModel):
    workspace_id: str
    task_id: str
    key: str
    deleted_at: str
    cleanup_pending: bool = True
    problem: str | None = None


class DeletionRecord(TaskDeletion):
    paths: list[CleanupPath] = Field(default_factory=list)
    retry_after: float = 0


class TaskDeletionService:
    def __init__(self, store: Workspace):
        self.store = store

    def blocker(self, db: sqlite3.Connection, task: TaskRevision) -> str | None:
        # Archived tasks must pass the same ownership checks as visible tasks.
        issue = self.store.archive_issue(db, task.model_copy(update={"archived": False}))
        if issue:
            return issue.message.replace("archiving", "deleting")
        dependent = db.execute(
            "SELECT t.key FROM task_dependencies d JOIN tasks t ON "
            "t.workspace_id=d.workspace_id AND t.id=d.task_id "
            "WHERE d.workspace_id=? AND d.prerequisite_id=? LIMIT 1",
            (task.workspace_id, task.id),
        ).fetchone()
        if dependent:
            return (
                f"{dependent[0]} depends on this task. Remove that dependency or archive instead."
            )
        # A frozen attempt may still need evidence even after its live dependency was edited.
        reference = db.execute(
            "SELECT t.key FROM runs r JOIN tasks t ON t.workspace_id=r.workspace_id "
            "AND t.id=r.task_id, json_each(json_extract(r.assignment,'$.prerequisites')) p "
            "WHERE r.workspace_id=? AND r.task_id!=? AND "
            "(json_extract(p.value,'$.task_id')=? OR json_extract(p.value,'$.task')=?) LIMIT 1",
            (task.workspace_id, task.id, task.id, task.key),
        ).fetchone()
        if reference:
            return (
                f"{reference[0]} retains this task's evidence. Archive instead or delete it first."
            )
        files = json.dumps(
            [
                row[0]
                for row in db.execute(
                    "SELECT id FROM output_files WHERE run_id IN "
                    "(SELECT id FROM runs WHERE workspace_id=? AND task_id=?) UNION ALL "
                    "SELECT id FROM attachments WHERE workspace_id=? AND task_id=?",
                    (task.workspace_id, task.id, task.workspace_id, task.id),
                )
            ]
        )
        selected = db.execute(
            "SELECT key FROM tasks t WHERE workspace_id=? AND id!=? AND EXISTS "
            "(SELECT 1 FROM json_each(t.data,'$.input_files') f "
            "WHERE f.value IN (SELECT value FROM json_each(?))) UNION ALL "
            "SELECT t.key FROM runs r JOIN tasks t ON t.workspace_id=r.workspace_id "
            "AND t.id=r.task_id WHERE r.workspace_id=? AND r.task_id!=? AND EXISTS "
            "(SELECT 1 FROM json_each(json_extract(r.assignment,'$.files')) f "
            "WHERE json_extract(f.value,'$.id') IN (SELECT value FROM json_each(?))) LIMIT 1",
            (task.workspace_id, task.id, files, task.workspace_id, task.id, files),
        ).fetchone()
        if selected:
            return (
                f"{selected[0]} uses this task's files. Archive instead or delete that task first."
            )
        return None

    def delete(self, workspace: str, identity: str, request: Edit) -> TaskDeletion:
        # Lock order matches inspection/delivery: repository first, then the DB writer.
        with self.store.connection() as db:
            self.store._workspace(db, workspace)
            saved = self._deleted(db, workspace, identity)
            task = None if saved else self.store._task(db, workspace, identity)
            repository = (
                Path(self.store._project(db, task.project_id).path)
                if task and task.project_id
                else None
            )
        if saved:
            try:
                return self.cleanup(workspace, saved.task_id)
            except ApplicationError as error:
                if error.code != "storage_busy":
                    raise
                return TaskDeletion.model_validate(saved.model_dump())
        assert task
        if repository and not self._has_git(repository):
            repository = None
        with gitops.lock(repository) if repository else nullcontext():
            with self.store.connection(write=True, workspace_id=workspace) as db:
                task = self.store._task(db, workspace, task.id)
                self.store._current(task.revision, request.expected_revision)
                if problem := self.blocker(db, task):
                    raise ApplicationError("task_delete_blocked", problem, 409)
                record = DeletionRecord(
                    workspace_id=workspace,
                    task_id=task.id,
                    key=task.key,
                    deleted_at=now(),
                    paths=self._paths(db, task, repository),
                )
                number = db.execute(
                    "SELECT number FROM tasks WHERE workspace_id=? AND id=?", (workspace, task.id)
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO task_deletions VALUES (?,?,?,?,?)",
                    (workspace, task.id, task.key, number, record.model_dump_json()),
                )
                self._remove_records(db, workspace, task.id)
        try:
            return self.cleanup(workspace, task.id)
        except ApplicationError as error:
            if error.code != "storage_busy":
                raise
            return TaskDeletion.model_validate(record.model_dump())

    @staticmethod
    def _deleted(db: sqlite3.Connection, workspace: str, identity: str) -> DeletionRecord | None:
        row = db.execute(
            "SELECT data FROM task_deletions WHERE workspace_id=? AND (task_id=? OR key=?)",
            (workspace, identity, identity.upper()),
        ).fetchone()
        return DeletionRecord.model_validate_json(row[0]) if row else None

    @staticmethod
    def _paths(
        db: sqlite3.Connection, task: TaskRevision, repository: Path | None
    ) -> list[CleanupPath]:
        paths: list[CleanupPath] = []
        owner = (task.workspace_id, task.id)
        groups = [
            ("", "SELECT id FROM runs WHERE workspace_id=? AND task_id=?"),
            (
                "",
                "SELECT id FROM review_attempts WHERE review_id IN "
                "(SELECT id FROM task_reviews WHERE workspace_id=? AND task_id=?)",
            ),
            (
                "integration-work/",
                "SELECT id FROM integrations WHERE run_id IN "
                "(SELECT id FROM runs WHERE workspace_id=? AND task_id=?)",
            ),
            (
                "inspection-work/",
                "SELECT id FROM inspections WHERE result_id IN "
                "(SELECT id FROM result_versions WHERE workspace_id=? AND task_id=?)",
            ),
        ]
        for prefix, query in groups:
            for (identity,) in db.execute(query, owner):
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identity):
                    raise ApplicationError("unsafe_cleanup", "Unexpected work directory identity.")
                paths.append(
                    CleanupPath(
                        relative=f"{prefix}local-attempts/{identity}",
                        repository=str(repository) if repository else None,
                    )
                )
                if not prefix:
                    paths.append(
                        CleanupPath(relative=f"workspaces/{task.workspace_id}/attempts/{identity}")
                    )
        for (identity,) in db.execute(
            "SELECT id FROM output_files WHERE run_id IN "
            "(SELECT id FROM runs WHERE workspace_id=? AND task_id=?)",
            owner,
        ):
            paths.extend(
                CleanupPath(relative=f"workspaces/{task.workspace_id}/outputs/{identity}{suffix}")
                for suffix in ("", ".partial")
            )
        return paths

    @staticmethod
    def _remove_records(db: sqlite3.Connection, workspace: str, task: str) -> None:
        owner = (workspace, task)
        # Gather notification identities before deleting their source rows.
        identities = {
            row[0]
            for table in ("runs", "result_versions", "questions", "agent_permissions")
            for row in db.execute(
                f"SELECT id FROM {table} WHERE workspace_id=? AND task_id=?", owner
            )
        }
        identities.update(
            row[0]
            for row in db.execute(
                "SELECT id FROM integrations WHERE run_id IN "
                "(SELECT id FROM runs WHERE workspace_id=? AND task_id=?)",
                owner,
            )
        )
        for table, column in (("announcement_events", "source_key"), ("attention_sources", "key")):
            for row in db.execute(
                f"SELECT {column} FROM {table} WHERE workspace_id=?", (workspace,)
            ).fetchall():
                if row[0].rsplit(":", 1)[-1] in identities:
                    db.execute(f"DELETE FROM {table} WHERE {column}=?", (row[0],))
        db.execute("DELETE FROM search_docs WHERE workspace_id=? AND task_id=?", owner)
        # Children first; foreign-key checking remains enabled throughout.
        statements = [
            "DELETE FROM inspections WHERE result_id IN "
            "(SELECT id FROM result_versions WHERE workspace_id=? AND task_id=?)",
            "DELETE FROM review_attempts WHERE review_id IN "
            "(SELECT id FROM task_reviews WHERE workspace_id=? AND task_id=?)",
            "DELETE FROM task_reviews WHERE workspace_id=? AND task_id=?",
            "DELETE FROM result_versions WHERE workspace_id=? AND task_id=?",
            "DELETE FROM integrations WHERE run_id IN "
            "(SELECT id FROM runs WHERE workspace_id=? AND task_id=?)",
            "DELETE FROM output_files WHERE run_id IN "
            "(SELECT id FROM runs WHERE workspace_id=? AND task_id=?)",
            "DELETE FROM execution_local WHERE run_id IN "
            "(SELECT id FROM runs WHERE workspace_id=? AND task_id=?)",
            "DELETE FROM run_activity WHERE run_id IN "
            "(SELECT id FROM runs WHERE workspace_id=? AND task_id=?)",
            "DELETE FROM runs WHERE workspace_id=? AND task_id=?",
            "DELETE FROM activity WHERE workspace_id=? AND task_id=?",
            "DELETE FROM question_revisions WHERE workspace_id=? AND question_id IN "
            "(SELECT id FROM questions WHERE workspace_id=question_revisions.workspace_id "
            "AND task_id=?)",
            "DELETE FROM questions WHERE workspace_id=? AND task_id=?",
            "DELETE FROM agent_permissions WHERE workspace_id=? AND task_id=?",
            "DELETE FROM attachments WHERE workspace_id=? AND task_id=?",
            "DELETE FROM task_replies WHERE workspace_id=? AND task_id=?",
            "DELETE FROM task_assemblies WHERE workspace_id=? AND task_id=?",
            "DELETE FROM subtask_dependencies WHERE workspace_id=? AND task_id=?",
            "DELETE FROM subtask_revisions WHERE workspace_id=? AND task_id=?",
            "DELETE FROM subtasks WHERE workspace_id=? AND task_id=?",
            "DELETE FROM task_dependencies WHERE workspace_id=? AND task_id=?",
            "DELETE FROM task_revisions WHERE workspace_id=? AND task_id=?",
            "DELETE FROM agent_settings WHERE workspace_id=? AND scope=?",
            "DELETE FROM reviewer_settings WHERE workspace_id=? AND entity_id=?",
            "DELETE FROM tasks WHERE workspace_id=? AND id=?",
        ]
        for statement in statements:
            db.execute(statement, owner)

    def cleanup(self, workspace: str, task: str) -> TaskDeletion:
        # A single short-lived owner prevents HTTP retry and restart cleanup overlapping.
        with acquire_lock(self.store.directory, ".task-cleanup.lock"):
            with self.store.connection() as db:
                record = self._deleted(db, workspace, task)
            if not record:
                raise ApplicationError("not_found", "Deleted task not found.", 404)
            if record.cleanup_pending:
                try:
                    for path in record.paths:
                        self._remove_path(path)
                    record.paths = []
                    record.cleanup_pending = False
                    record.problem = None
                except (OSError, ApplicationError) as error:
                    record.retry_after = time.time() + 30
                    record.problem = "Task deleted; file cleanup is pending. Flowfield will retry."
                    logging.getLogger(__name__).warning("Task file cleanup pending: %s", error)
                with self.store.connection(write=True, workspace_id=workspace) as db:
                    db.execute(
                        "UPDATE task_deletions SET data=? WHERE workspace_id=? AND task_id=?",
                        (record.model_dump_json(), workspace, task),
                    )
            return TaskDeletion.model_validate(record.model_dump())

    @staticmethod
    def _has_git(repository: Path) -> bool:
        # Report-only projects can be ordinary directories; removed repositories
        # cannot own a concurrent Git operation but owned files still need cleanup.
        return repository.exists() and bool(
            git(repository, "rev-parse", "--git-common-dir", allowed_returncodes=(0, 128)).strip()
        )

    def _remove_path(self, item: CleanupPath) -> None:
        relative = Path(item.relative)
        name = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
        allowed = (
            rf"(?:(?:(?:inspection|integration)-work/)?local-attempts/{name}|"
            rf"workspaces/{name}/(?:attempts/{name}|outputs/{name}(?:\.partial)?))"
        )
        if not re.fullmatch(allowed, item.relative):
            raise ApplicationError("unsafe_cleanup", "Cleanup path is outside the service store.")
        path = self.store.directory / relative
        if not path.parent.resolve().is_relative_to(self.store.directory):
            raise ApplicationError("unsafe_cleanup", "Cleanup parent traverses a symlink.")
        if path.is_symlink():
            path.unlink()  # Never follow links into adopted repositories or other user data.
            return
        checkout = path / "worktree"
        if checkout.is_symlink():
            checkout.unlink()
        if item.repository and self._has_git(Path(item.repository)):
            repository = Path(item.repository)
            with gitops.lock(repository):
                target = str(path / "worktree").encode()
                registered = git(repository, "worktree", "list", "--porcelain", "-z").split(b"\0")
                if b"worktree " + target in registered:
                    git(repository, "worktree", "remove", "--force", str(path / "worktree"))
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def retry_cleanup(self, *, limit: int = 1) -> None:
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT workspace_id,task_id FROM task_deletions WHERE "
                "json_extract(data,'$.cleanup_pending')=1 "
                "AND coalesce(json_extract(data,'$.retry_after'),0)<=? "
                "ORDER BY coalesce(json_extract(data,'$.retry_after'),0) LIMIT ?",
                (time.time(), limit),
            ).fetchall()
        for workspace, task in rows:
            try:
                self.cleanup(workspace, task)
            except ApplicationError as error:
                if error.code != "storage_busy":
                    raise
