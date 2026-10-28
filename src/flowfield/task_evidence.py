"""Bounded discovery and exact-byte retrieval of a task's retained evidence.

Historical files are available for assessment, not silently selected as new deliverables.
The caller supplies the frozen run/result boundary and an owned materialization directory.
"""

import hashlib
import json
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.output_models import FileInput
from flowfield.outputs import Outputs
from flowfield.task_files import materialize


class EvidenceFileRequest(Record):
    file_id: str = Field(min_length=1, max_length=100)


def review_files(db: sqlite3.Connection, workspace: str, run: str) -> list[dict[str, Any]]:
    """Bounded review artifact index for an immutable implementation result."""
    rows = db.execute(
        "SELECT o.data,a.data attempt FROM output_files o JOIN review_attempts a "
        "ON a.id=o.review_attempt_id JOIN result_versions v "
        "ON v.id=json_extract(o.data,'$.result_id') WHERE o.workspace_id=? AND v.run_id=? "
        "ORDER BY o.number DESC LIMIT 32",
        (workspace, run),
    ).fetchall()
    return [
        {
            "file": {
                key: json.loads(row["data"]).get(key)
                for key in (
                    "id",
                    "name",
                    "description",
                    "size",
                    "sha256",
                    "review_attempt_id",
                    "result_id",
                )
            },
            "status": json.loads(row["attempt"]).get("status"),
            "problem": json.loads(row["attempt"]).get("problem"),
            "cleanup_confirmed": json.loads(row["attempt"]).get("cleanup_confirmed"),
        }
        for row in rows
    ]


def prior_results(store: Workspace, workspace: str, task: str, before: int) -> str:
    with store.connection() as db:
        rows = db.execute(
            "SELECT version,data FROM result_versions WHERE workspace_id=? AND task_id=? "
            "AND version<? ORDER BY version DESC LIMIT 6",
            (workspace, task, before),
        ).fetchall()
    items = []
    for row in rows[:5]:
        value = json.loads(row["data"])
        items.append(
            {
                "id": value["id"],
                "version": value["version"],
                "source_commit": value["source_commit"],
                "candidate_commit": value.get("candidate_commit"),
                "summary": value["report"]["summary"][:300],
                "section": "prior_result:" + value["id"],
            }
        )
    return json.dumps(
        {
            "authority": "Historical evidence with its original source; not current scope, "
            "deliverable selection, proof of successor code or delivery approval.",
            "items": items,
            "next_section": f"prior_results:{rows[4]['version']}" if len(rows) > 5 else None,
        }
    )


def prior_result(store: Workspace, workspace: str, task: str, before: int, identity: str) -> str:
    with store.connection() as db:
        row = db.execute(
            "SELECT data FROM result_versions WHERE workspace_id=? AND task_id=? "
            "AND version<? AND id=?",
            (workspace, task, before, identity),
        ).fetchone()
        if not row:
            raise ApplicationError(
                "evidence_unavailable", "Result is outside this evidence boundary.", 404
            )
        value = json.loads(row[0])
        files = Outputs.selected(db, workspace, value.get("output_ids", []))
        reviews = db.execute(
            "SELECT data FROM task_reviews WHERE workspace_id=? AND result_id=? "
            "ORDER BY number DESC LIMIT 3",
            (workspace, identity),
        ).fetchall()
        verification_files = review_files(db, workspace, value["run_id"])
    return json.dumps(
        {
            "id": identity,
            "version": value["version"],
            "source_commit": value["source_commit"],
            "candidate_commit": value.get("candidate_commit"),
            "report": value["report"],
            "verification_files": verification_files,
            "files": [
                file.model_dump(include={"id", "name", "description", "sha256", "size", "run_id"})
                for file in files
            ],
            "reviews": [
                {
                    "id": saved["id"],
                    "binding": saved.get("binding"),
                    "verdict": saved.get("verdict"),
                }
                for saved in (json.loads(row[0]) for row in reviews)
            ],
        }
    )


def read_file(
    store: Workspace,
    workspace: str,
    task: str,
    identity: str,
    root: Path,
    *,
    run_ids: set[str] | None = None,
    result_before: int | None = None,
) -> str:
    try:
        file = Outputs(store).get(workspace, identity)
    except ApplicationError as error:
        if error.code != "not_found" or result_before is None:
            raise
        return read_observation_file(store, workspace, task, identity, root, result_before)
    if file.task_id != task:
        raise ApplicationError("evidence_unavailable", "File belongs to another task.", 403)
    source_result = None
    if file.review_attempt_id:
        with store.connection() as db:
            source = db.execute(
                "SELECT r.result_id FROM review_attempts a JOIN task_reviews r ON r.id=a.review_id "
                "WHERE a.id=? AND r.workspace_id=? AND r.task_id=?",
                (file.review_attempt_id, workspace, task),
            ).fetchone()
            source_result = source[0] if source else None
    allowed = run_ids is not None and file.run_id is not None and file.run_id in run_ids
    if run_ids and source_result:
        with store.connection() as db:
            row = db.execute(
                "SELECT run_id FROM result_versions WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace, task, source_result),
            ).fetchone()
            allowed = bool(row and row[0] in run_ids)
    if result_before is not None:
        with store.connection() as db:
            allowed = bool(
                db.execute(
                    "SELECT 1 FROM result_versions v,json_each(v.data,'$.output_ids') f "
                    "WHERE v.workspace_id=? AND v.task_id=? AND v.version<? AND f.value=? LIMIT 1",
                    (workspace, task, result_before, identity),
                ).fetchone()
            )
            if source_result:
                allowed = bool(
                    db.execute(
                        "SELECT 1 FROM result_versions WHERE workspace_id=? AND task_id=? "
                        "AND id=? AND version<?",
                        (workspace, task, source_result, result_before),
                    ).fetchone()
                )
    if not allowed:
        raise ApplicationError(
            "evidence_unavailable", "File is outside this attempt's evidence boundary.", 403
        )
    target = root / file.id / PurePosixPath(file.name).name
    if any(path.is_symlink() for path in (target, *target.parents)):
        raise ApplicationError("evidence_changed", "Evidence copy contains a symlink.", 409)
    if target.exists():
        with target.open("rb") as stream:
            valid = (
                target.stat().st_size == file.size
                and hashlib.file_digest(stream, "sha256").hexdigest() == file.sha256
            )
        if not valid:
            raise ApplicationError("evidence_changed", "The attempt's evidence copy changed.", 409)
    else:
        materialize(
            store,
            workspace,
            [
                FileInput(
                    id=file.id, kind="output", name=file.name, size=file.size, sha256=file.sha256
                )
            ],
            root,
        )
    return json.dumps(
        {
            "id": file.id,
            "name": file.name,
            "path": str(target),
            "sha256": file.sha256,
            "source_run_id": file.run_id,
            "source_review_attempt_id": file.review_attempt_id,
            "authority": "Read-only historical evidence; "
            "verify applicability to the current outcome.",
        }
    )


def read_observation_file(
    store: Workspace, workspace: str, task: str, identity: str, root: Path, before: int
) -> str:
    """Only immutable attachments actually sent in result-bound observations."""
    href = f"/api/workspaces/{workspace}/attachments/{identity}"
    with store.connection() as db:
        row = db.execute(
            "SELECT a.* FROM attachments a WHERE a.id=? AND a.workspace_id=? AND a.task_id=? "
            "AND a.bound=1 AND EXISTS (SELECT 1 FROM task_replies r JOIN result_versions v "
            "ON v.id=json_extract(r.data,'$.binding.result_id') WHERE r.workspace_id=? "
            "AND r.task_id=? AND v.version<? AND json_extract(r.data,'$.action')='observation' "
            "AND instr(json_extract(r.data,'$.body'),?)>0)",
            (identity, workspace, task, workspace, task, before, href),
        ).fetchone()
        if not row:
            raise ApplicationError(
                "evidence_unavailable", "File is outside this result's observations.", 403
            )
        file = FileInput(
            id=identity,
            kind="attachment",
            name=row["name"],
            size=row["size"],
            sha256=hashlib.sha256(row["content"]).hexdigest(),
        )
    target = root / identity / file.name
    if any(path.is_symlink() for path in (target, *target.parents)):
        raise ApplicationError("evidence_changed", "Evidence copy contains a symlink.", 409)
    if target.exists():
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != file.sha256:
                raise ApplicationError(
                    "evidence_changed", "The attempt's evidence copy changed.", 409
                )
    else:
        materialize(store, workspace, [file], root)
    return json.dumps(
        {
            "id": identity,
            "name": file.name,
            "path": str(target),
            "sha256": file.sha256,
            "authority": "Human observation attachment; not changed scope or approval.",
        }
    )
