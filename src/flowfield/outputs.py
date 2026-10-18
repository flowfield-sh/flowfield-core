"""Capture immutable submitted files; attempts never own their retained lifetime."""

import hashlib
import json
import mimetypes
import os
import sqlite3
import stat
from pathlib import Path
from uuid import uuid4

from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import Run
from flowfield.output_models import OutputFile, OutputPage, OutputSelection

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
        self, db: sqlite3.Connection, run: Run, selections: list[OutputSelection]
    ) -> list[OutputFile]:
        if not selections:
            return []
        if len({item.path for item in selections}) != len(selections):
            raise ApplicationError("output_duplicate", "Submit each file only once.")
        owner = run.workspace_id
        task = self.store._task(db, run.workspace_id, run.task_id)
        row = db.execute("SELECT data FROM execution_local WHERE run_id=?", (run.id,)).fetchone()
        metadata = json.loads(row[0]) if row else {}
        if not metadata.get("runtime"):
            raise ApplicationError("output_unavailable", "This attempt has no output directory.")
        source = Path(metadata["runtime"]) / "output"
        copied: list[OutputFile] = []
        total = 0
        # Publish references only after immutable bytes are durable.
        for selection in selections:
            value = OutputFile(
                id=uuid4().hex,
                workspace_id=owner,
                project_id=run.project_id,
                task_id=task.id,
                task_key=task.key,
                task_title=task.title,
                run_id=run.id,
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
                destination.parent.mkdir(parents=True, exist_ok=True)
                descriptor = self._source(source, selection.path)
                with os.fdopen(descriptor, "rb") as incoming, staging.open("xb") as outgoing:
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
                os.replace(staging, destination)
                parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            except (OSError, ValueError) as error:
                staging.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                for previous in copied:
                    self._path(previous).unlink(missing_ok=True)
                raise ApplicationError(
                    "output_capture_failed",
                    f"Could not retain {selection.path}: {error}. "
                    "Keep files inside FLOWFIELD_OUTPUT_DIR and submit again.",
                    409,
                ) from error
            copied.append(value)
        for value in copied:
            db.execute(
                "INSERT INTO output_files(id,workspace_id,run_id,data) VALUES (?,?,?,?)",
                (value.id, owner, run.id, value.model_dump_json()),
            )
        return copied

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
