"""Immutable human attachments referenced by canonical messages, not browser storage."""

import base64
import binascii
import re
import sqlite3
from collections.abc import Callable
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.workspaces import Workspaces

MAX_FILE = 2 * 1024 * 1024
MAX_TEXT = 64 * 1024
MAX_FILES = 4
REFERENCE = re.compile(
    r"/api/(projects|workspaces)/([a-zA-Z0-9_-]+)/attachments/([a-f0-9]{32})(?![a-f0-9])"
)


class Attachment(BaseModel):
    id: str
    name: str
    mime: str
    size: int
    href: str


class AttachmentUpload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mime: str = Field(max_length=100)
    data: str = Field(max_length=MAX_FILE * 4 // 3 + 4)
    task_id: str | None = None


class Attachments:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def upload(
        self, project: str, request: AttachmentUpload, *, workspace_scope: bool = False
    ) -> Attachment:
        if workspace_scope and request.task_id:
            raise ApplicationError(
                "scope_mismatch", "Workspace attachments belong to coordinator chat.", 403
            )
        if not workspace_scope and request.task_id is None and self.workspace.schema_version >= 59:
            project = Workspaces(self.workspace).for_project(project).id
            workspace_scope = True
        if any(ord(c) < 32 or c in "/\\" for c in request.name):
            raise ApplicationError("attachment_name", "Choose a file with a simple filename.")
        try:
            content = base64.b64decode(request.data, validate=True)
        except (ValueError, binascii.Error):
            raise ApplicationError("attachment_data", "The file could not be decoded.") from None
        if not content or len(content) > MAX_FILE:
            raise ApplicationError("attachment_size", "Files must be nonempty and at most 2 MiB.")
        mime = request.mime
        signatures = {
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
        }
        if mime.startswith("image/"):
            if not signatures.get(mime):
                raise ApplicationError("attachment_type", "Use a PNG, JPEG or WebP image.")
        else:
            try:
                text = content.decode("utf-8")
            except UnicodeError:
                raise ApplicationError(
                    "attachment_type", "Use UTF-8 text/code or a PNG, JPEG or WebP image."
                ) from None
            if "\x00" in text or len(content) > MAX_TEXT:
                raise ApplicationError(
                    "attachment_type", "Text/code files must be UTF-8 and at most 64 KiB."
                )
            mime = "text/plain"
        identity = uuid4().hex
        with self.workspace.connection(write=True, notify=False) as db:
            if workspace_scope:
                Workspaces.read(db, project)
            else:
                self.workspace._work_scope(db, project)
            owner = "workspace_id" if workspace_scope else "project_id"
            task = (
                self.workspace._task(db, project, request.task_id).id if request.task_id else None
            )
            # Abandoned drafts expire; files referenced by saved messages never do.
            db.execute(
                "DELETE FROM attachments WHERE bound=0 AND created_at < "
                "strftime('%Y-%m-%dT%H:%M:%f','now','-1 day')"
            )
            total = db.execute(
                f"SELECT coalesce(sum(length(content)),0) FROM attachments WHERE {owner}=?",
                (project,),
            ).fetchone()[0]
            if total + len(content) > 128 * 1024 * 1024:
                raise ApplicationError(
                    "attachment_capacity", "This attachment storage is full.", 409
                )
            db.execute(
                f"INSERT INTO attachments(id,{owner},task_id,name,mime,created_at,"
                "content,size,bound) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (identity, project, task, request.name, mime, now(), content, len(content)),
            )
        kind = "workspaces" if workspace_scope else "projects"
        return Attachment(
            id=identity,
            name=request.name,
            mime=mime,
            size=len(content),
            href=f"/api/{kind}/{project}/attachments/{identity}",
        )

    def references(
        self,
        db: sqlite3.Connection,
        project: str,
        task: str | None,
        text: str,
        *,
        bind: bool = False,
        limit: int = MAX_FILES,
        workspace_scope: bool = False,
    ) -> list[sqlite3.Row]:
        identities = list(dict.fromkeys(REFERENCE.findall(text)))
        if len(identities) > limit:
            raise ApplicationError("attachment_count", "Attach at most four files per message.")
        rows = []
        for kind, owner, identity in identities:
            column = "workspace_id" if workspace_scope else "project_id"
            row = db.execute(
                f"SELECT * FROM attachments WHERE id=? AND {column}=?", (identity, project)
            ).fetchone()
            expected_kind = "workspaces" if workspace_scope else "projects"
            matches = kind == expected_kind and owner == project
            if workspace_scope and row and kind == "projects":
                matches = row["legacy_project_id"] == owner
            if not matches or not row or row["task_id"] != task:
                raise ApplicationError(
                    "attachment_missing",
                    "An attachment is unavailable for this conversation. Attach it again.",
                    409,
                )
            if not bind and not row["bound"]:
                raise ApplicationError(
                    "attachment_unbound", "The attachment has not been sent.", 409
                )
            if bind:
                db.execute("UPDATE attachments SET bound=1 WHERE id=?", (identity,))
            rows.append(row)
        return rows

    def inputs(
        self, project: str, task: str | None, text: str, *, workspace_scope: bool = False
    ) -> list[dict[str, str]]:
        with self.workspace.connection() as db:
            return [
                {
                    "name": row["name"],
                    "mime": row["mime"],
                    "data": base64.b64encode(row["content"]).decode(),
                }
                for row in self.references(
                    db, project, task, text, limit=8, workspace_scope=workspace_scope
                )
            ]


def attachment_router(
    workspace: Callable[[], Workspace], *, workspace_scope: bool = False
) -> APIRouter:
    router = APIRouter(
        prefix="/api/"
        + ("workspaces" if workspace_scope else "projects")
        + "/{project_id}/attachments"
    )

    @router.post(
        "",
        response_model=Attachment,
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": AttachmentUpload.model_json_schema()}},
            }
        },
    )
    async def upload(project_id: str, request: Request) -> Attachment:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_FILE * 4 // 3 + 4096:
                raise ApplicationError("attachment_size", "Files must be at most 2 MiB.", 413)
        try:
            value = AttachmentUpload.model_validate_json(body)
        except ValidationError:
            raise ApplicationError(
                "attachment_invalid", "The attachment upload is invalid."
            ) from None
        return Attachments(workspace()).upload(project_id, value, workspace_scope=workspace_scope)

    @router.get("/{identity}")
    def download(project_id: str, identity: str) -> Response:
        with workspace().connection() as db:
            row = db.execute(
                (
                    "SELECT name,mime,content FROM attachments WHERE workspace_id=? AND id=?"
                    if workspace_scope
                    else "SELECT name,mime,content FROM attachments "
                    "WHERE (project_id=? OR legacy_project_id=?) AND id=?"
                ),
                (project_id, identity) if workspace_scope else (project_id, project_id, identity),
            ).fetchone()
            if not row:
                raise ApplicationError("attachment_missing", "Attachment not found.", 404)
            return Response(
                row["content"],
                media_type=row["mime"],
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''"
                    + quote(row["name"], safe="")
                },
            )

    return router
