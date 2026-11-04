"""Capture immutable submitted files; attempts never own their retained lifetime."""

import hashlib
import json
import logging
import mimetypes
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from flowfield.application import Task, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import Run
from flowfield.output_models import OutputFile, OutputPage, OutputSelection
from flowfield.storage import acquire_lock

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_SUBMISSION_BYTES = 512 * 1024 * 1024


class Outputs:
    def __init__(self, store: Workspace):
        self.store = store

    def _path(self, value: OutputFile) -> Path:
        return self.store.directory / "workspaces" / value.workspace_id / "outputs" / value.id

    @staticmethod
    def _source(root: Path, relative: str) -> int:
        """Walk directories by descriptor; reject symlinks, devices and traversal."""
        parts = [*root.absolute().parts[1:], *relative.split("/")]
        directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = child
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            source_stat = os.fstat(descriptor)
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
                os.close(descriptor)
                raise ValueError("Only regular files without shared links can be submitted.")
            return descriptor
        finally:
            os.close(directory)

    def capture(
        self, run: Run, selections: list[OutputSelection]
    ) -> AbstractContextManager[list[OutputFile]]:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT data FROM execution_local WHERE run_id=?", (run.id,)
            ).fetchone()
        metadata = json.loads(row[0]) if row else {}
        return self.capture_owned(
            run.workspace_id, run.task_id, selections, metadata, run_id=run.id
        )

    @contextmanager
    def capture_owned(
        self,
        owner: str,
        task_id: str,
        selections: list[OutputSelection],
        metadata: dict[str, Any],
        *,
        run_id: str | None = None,
        review_attempt_id: str | None = None,
        result_id: str | None = None,
        inspection_id: str | None = None,
        runtime_generation: str | None = None,
    ) -> Iterator[list[OutputFile]]:
        if not selections:
            yield []
            return
        if sum(value is not None for value in (run_id, review_attempt_id, inspection_id)) != 1:
            raise ValueError("Evidence requires exactly one execution owner.")
        if len({item.path for item in selections}) != len(selections):
            raise ApplicationError("output_duplicate", "Submit each file only once.")
        with self.store.connection() as db:
            task = self.store._task(db, owner, task_id)
        if not metadata.get("runtime"):
            raise ApplicationError("output_unavailable", "This attempt has no output directory.")
        source = Path(metadata["runtime"]) / "output"
        # Captures may run concurrently. Recovery alone needs exclusive ownership;
        # neither copying nor cleanup owns SQLite's writer. Death releases this lock.
        with acquire_lock(
            self.store.directory,
            ".output-capture.lock",
            shared=True,
            busy_message="Output cleanup is running. Retry this submission shortly.",
        ):
            created: set[Path] = set()
            try:
                yield self._copy(
                    task,
                    selections,
                    source,
                    created,
                    run_id,
                    review_attempt_id,
                    result_id,
                    inspection_id,
                    runtime_generation,
                )
            finally:
                # Check actual commit evidence, including when an after-commit
                # notification fails. An exception never authorizes deleting saved files.
                try:
                    retained = self._referenced(owner, [p.name for p in created])
                    for path in created:
                        if path.name.removesuffix(".partial") not in retained:
                            path.unlink(missing_ok=True)
                except (ApplicationError, OSError, sqlite3.Error):
                    logging.getLogger(__name__).warning("Output capture cleanup pending")

    def _copy(
        self,
        task: Task,
        selections: list[OutputSelection],
        source: Path,
        created: set[Path],
        run_id: str | None,
        review_attempt_id: str | None,
        result_id: str | None,
        inspection_id: str | None,
        runtime_generation: str | None,
    ) -> list[OutputFile]:
        copied: list[OutputFile] = []
        total = 0
        # Publish references only after immutable bytes are durable.
        for selection in selections:
            value = OutputFile(
                id=uuid4().hex,
                workspace_id=task.workspace_id,
                project_id=task.project_id,
                task_id=task.id,
                task_key=task.key,
                task_title=task.title,
                run_id=run_id,
                review_attempt_id=review_attempt_id,
                result_id=result_id,
                inspection_id=inspection_id,
                runtime_generation=runtime_generation,
                name=selection.path,
                description=selection.description,
                media_type=mimetypes.guess_type(selection.path)[0] or "application/octet-stream",
                size=0,
                sha256="",
                created_at=now(),
            )
            destination = self._path(value)
            staging = destination.with_suffix(".partial")
            try:
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                descriptor = self._source(source, selection.path)
                with os.fdopen(descriptor, "rb") as incoming, staging.open("xb") as outgoing:
                    created.add(staging)
                    before = os.fstat(incoming.fileno())
                    digest = hashlib.sha256()
                    while block := incoming.read(1024 * 1024):
                        value.size += len(block)
                        total += len(block)
                        if value.size > MAX_FILE_BYTES or total > MAX_SUBMISSION_BYTES:
                            raise ValueError(
                                "Submit at most 128 MiB per file and 512 MiB per result."
                            )
                        digest.update(block)
                        outgoing.write(block)
                    after = os.fstat(incoming.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ):
                        raise ValueError(
                            "The file changed during capture; finish writing it first."
                        )
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                    value.sha256 = digest.hexdigest()
                # Unlike replace(), link cannot overwrite even an unexpected UUID collision.
                os.link(staging, destination)
                created.add(destination)
                staging.unlink()
                parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            except (OSError, ValueError) as error:
                raise ApplicationError(
                    "output_capture_failed",
                    f"Could not retain {selection.path}: {error}. "
                    "Keep files inside FLOWFIELD_OUTPUT_DIR and submit again.",
                    409,
                ) from error
            copied.append(value)
        return copied

    @staticmethod
    def publish(db: sqlite3.Connection, files: list[OutputFile]) -> None:
        """Caller rechecks current submission ownership in this short transaction."""
        for value in files:
            db.execute(
                "INSERT INTO output_files"
                "(id,workspace_id,run_id,data,review_attempt_id,inspection_id) "
                "VALUES (?,?,?,?,?,?)",
                (
                    value.id,
                    value.workspace_id,
                    value.run_id,
                    value.model_dump_json(),
                    value.review_attempt_id,
                    value.inspection_id,
                ),
            )

    def _referenced(self, owner: str, names: list[str]) -> set[str]:
        if not names:
            return set()
        with self.store.connection() as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT id FROM output_files WHERE workspace_id=? AND id IN "
                    "(SELECT value FROM json_each(?))",
                    (owner, json.dumps([name.removesuffix(".partial") for name in names])),
                )
            }

    def recover(self) -> None:
        """Remove abandoned captures while excluding only concurrent captures.

        Files become durable before their SQLite references. After process death,
        only committed references distinguish retained evidence from abandoned bytes.
        Work through directory descriptors so cleanup never follows a replaced link.
        """
        try:
            lock = acquire_lock(self.store.directory, ".output-capture.lock")
        except ApplicationError as error:
            if error.code != "storage_busy":
                raise
            return  # A capture still owns its bytes; reconcile on a later recovery.
        with lock:
            with self.store.connection() as db:
                owners = [row[0] for row in db.execute("SELECT id FROM workspaces")]
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            try:
                root = os.open(self.store.directory / "workspaces", flags)
            except FileNotFoundError:
                return
            except OSError:
                logging.getLogger(__name__).warning("Output cleanup could not open workspace files")
                return
            try:
                for identity in owners:
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identity):
                        continue
                    try:
                        self._recover_workspace(root, identity, flags)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        logging.getLogger(__name__).warning(
                            "Output cleanup pending for workspace %s", identity
                        )
            finally:
                os.close(root)

    def _recover_workspace(self, root: int, identity: str, flags: int) -> None:
        with ExitStack() as cleanup:
            directory = root
            for name in (identity, "outputs"):
                directory = os.open(name, flags, dir_fd=directory)
                cleanup.callback(os.close, directory)
            with os.scandir(directory) as entries:
                pending: list[str] = []
                for entry in entries:
                    if not re.fullmatch(r"[0-9a-f]{32}(?:\.partial)?", entry.name):
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    pending.append(entry.name)
                    if len(pending) == 128:
                        self._remove_abandoned(directory, identity, pending)
                        pending.clear()
                self._remove_abandoned(directory, identity, pending)

    def _remove_abandoned(self, directory: int, identity: str, names: list[str]) -> None:
        retained = self._referenced(identity, names)
        for name in names:
            if name.removesuffix(".partial") not in retained:
                os.unlink(name, dir_fd=directory)

    @staticmethod
    def for_run(db: sqlite3.Connection, run_id: str) -> list[OutputFile]:
        return [
            Outputs._present(db, OutputFile.model_validate_json(row[0]))
            for row in db.execute(
                "SELECT o.data FROM output_files o WHERE o.run_id=? OR EXISTS "
                "(SELECT 1 FROM result_versions v,json_each(v.data,'$.output_ids') f "
                "WHERE v.run_id=? AND f.value=o.id) ORDER BY o.number",
                (run_id, run_id),
            )
        ]

    @staticmethod
    def _present(db: sqlite3.Connection, file: OutputFile) -> OutputFile:
        row = db.execute(
            "SELECT v.id,v.version,v.status FROM result_versions "
            "v,json_each(v.data,'$.output_ids') f "
            "WHERE v.workspace_id=? AND f.value=? ORDER BY v.version DESC LIMIT 1",
            (file.workspace_id, file.id),
        ).fetchone()
        if row:
            file.result_id, file.result_version, file.result_status = row
        elif file.result_id:
            row = db.execute(
                "SELECT version,status FROM result_versions WHERE workspace_id=? AND id=?",
                (file.workspace_id, file.result_id),
            ).fetchone()
            if row:
                file.result_version, file.result_status = row
        return file

    @staticmethod
    def selected(
        db: sqlite3.Connection, workspace_id: str, identities: list[str]
    ) -> list[OutputFile]:
        files = []
        for identity in dict.fromkeys(identities):
            row = db.execute(
                "SELECT data FROM output_files WHERE workspace_id=? AND id=?",
                (workspace_id, identity),
            ).fetchone()
            if not row:
                raise ApplicationError("output_missing", "A selected output is unavailable.", 409)
            files.append(Outputs._present(db, OutputFile.model_validate_json(row[0])))
        return files

    def get(self, workspace_id: str, identity: str) -> OutputFile:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT data FROM output_files WHERE workspace_id=? AND id=?",
                (workspace_id, identity),
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "File not found in this workspace.", 404)
            return self._present(db, OutputFile.model_validate_json(row[0]))

    def page(
        self,
        workspace_id: str,
        *,
        before: int | None = None,
        limit: int = 30,
        task_key: str | None = None,
    ) -> OutputPage:
        from flowfield.workspaces import Workspaces

        limit = max(1, min(limit, 50))
        with self.store.connection() as db:
            Workspaces.read(db, workspace_id)
            rows = db.execute(
                "SELECT number,data FROM output_files WHERE workspace_id=? "
                "AND (? IS NULL OR number<?) "
                "AND (? IS NULL OR json_extract(data,'$.task_key')=?) "
                "ORDER BY number DESC LIMIT ?",
                (
                    workspace_id,
                    before,
                    before,
                    task_key,
                    task_key,
                    limit + 1,
                ),
            ).fetchall()
            items: list[OutputFile] = []
            size = 0
            for row in rows[:limit]:
                file = self._present(db, OutputFile.model_validate_json(row["data"]))
                size += len(file.model_dump_json().encode())
                if items and size > 22000:
                    break
                items.append(file)
            return OutputPage(
                items=items,
                next_before=rows[len(items) - 1]["number"] if len(rows) > len(items) else None,
            )

    def content(self, value: OutputFile) -> Path:
        path = self._path(value)
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size != value.size:
                raise ValueError("Retained file unavailable")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != value.sha256:
                    raise ValueError("Retained file changed")
        except (OSError, ValueError) as error:
            raise ApplicationError(
                "output_unavailable",
                "The retained file is missing or changed. "
                "Restore its original bytes from backup; its metadata is preserved.",
                409,
            ) from error
        return path
