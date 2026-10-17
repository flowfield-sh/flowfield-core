"""Freeze selected input files and copy exact versions into an attempt's own input area."""

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from flowfield.errors import ApplicationError
from flowfield.output_models import FileInput, OutputFile
from flowfield.outputs import MAX_SUBMISSION_BYTES, Outputs

if TYPE_CHECKING:
    from flowfield.application import Workspace


def freeze(db: sqlite3.Connection, workspace_id: str, identities: list[str]) -> list[FileInput]:
    if len(set(identities)) > 256:
        raise ApplicationError("input_files_count", "Use at most 256 input files per attempt.")
    result = []
    total = 0
    for identity in dict.fromkeys(identities):
        output = db.execute(
            "SELECT data FROM output_files WHERE workspace_id=? AND id=?", (workspace_id, identity)
        ).fetchone()
        if output:
            file = OutputFile.model_validate_json(output[0])
            value = FileInput(
                id=file.id, kind="output", name=file.name, size=file.size, sha256=file.sha256
            )
        else:
            attachment = db.execute(
                "SELECT name,size,content FROM attachments WHERE id=? AND bound=1 AND "
                "(workspace_id=? OR project_id IN "
                "(SELECT project_id FROM workspace_scopes WHERE workspace_id=?))",
                (identity, workspace_id, workspace_id),
            ).fetchone()
            if not attachment:
                raise ApplicationError(
                    "input_file_missing",
                    "Choose a submitted output or sent attachment in this workspace.",
                    409,
                )
            value = FileInput(
                id=identity,
                kind="attachment",
                name=attachment["name"],
                size=attachment["size"],
                sha256=hashlib.sha256(attachment["content"]).hexdigest(),
            )
        total += value.size
        if total > MAX_SUBMISSION_BYTES:
            raise ApplicationError("input_files_size", "Select at most 512 MiB of input files.")
        result.append(value)
    return result


def materialize(
    store: "Workspace", owner: str, files: list[FileInput], root: Path
) -> list[dict[str, str]]:
    result = []
    for file in files:
        target = root / file.id / PurePosixPath(file.name).name
        target.parent.mkdir(parents=True, exist_ok=False)
        if file.kind == "output":
            output = Outputs(store).get(owner, file.id)
            shutil.copyfile(Outputs(store).content(output), target)
        else:
            with store.connection() as db:
                row = db.execute(
                    "SELECT content FROM attachments WHERE id=? AND bound=1 AND "
                    "(workspace_id=? OR project_id IN "
                    "(SELECT project_id FROM workspace_scopes WHERE workspace_id=?))",
                    (file.id, owner, owner),
                ).fetchone()
                if not row:
                    raise ApplicationError(
                        "input_file_missing", "The selected input is unavailable.", 409
                    )
                target.write_bytes(row[0])
        with target.open("rb") as stream:
            if (
                os.fstat(stream.fileno()).st_size != file.size
                or hashlib.file_digest(stream, "sha256").hexdigest() != file.sha256
            ):
                raise ApplicationError(
                    "input_file_changed",
                    "A frozen input changed. Restore its exact version before retrying.",
                    409,
                )
        target.chmod(0o400)
        result.append({"id": file.id, "name": file.name, "path": str(target)})
    return result
