"""Workspace-scoped metadata and retained file downloads."""

import codecs
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from flowfield.application import Workspace
from flowfield.file_browser import (
    FileBrowser,
    FileDirectory,
    FileWorkPage,
)
from flowfield.output_models import OutputFile, OutputPage, OutputText
from flowfield.outputs import Outputs

# Never render active HTML/SVG or arbitrary worker-authored content on the app origin.
INLINE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "text/plain",
}


def output_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/files")

    @router.get("")
    def files(
        workspace_id: str,
        before: int | None = Query(None, ge=1),
        limit: int = Query(30, ge=1, le=50),
        task_key: str | None = None,
    ) -> OutputPage:
        return Outputs(workspace()).page(
            workspace_id,
            before=before,
            limit=limit,
            task_key=task_key,
        )

    @router.get("/browse")
    def browse(
        workspace_id: str,
        path: str = Query("", max_length=500),
        task_key: str | None = None,
        sort: Literal["name", "newest", "type"] = "name",
        offset: int = Query(0, ge=0),
    ) -> FileDirectory:
        return FileBrowser(workspace()).directory(
            workspace_id,
            path,
            task_key=task_key,
            sort=sort,
            offset=offset,
        )

    @router.get("/work")
    def work(workspace_id: str, offset: int = Query(0, ge=0)) -> FileWorkPage:
        return FileBrowser(workspace()).work(workspace_id, offset)

    @router.get("/{identity}")
    def file(workspace_id: str, identity: str) -> OutputFile:
        return Outputs(workspace()).get(workspace_id, identity)

    @router.get("/{identity}/content")
    def content(workspace_id: str, identity: str, download: bool = False) -> FileResponse:
        outputs = Outputs(workspace())
        value = outputs.get(workspace_id, identity)
        inline = not download and value.media_type in INLINE_TYPES
        return FileResponse(
            outputs.content(value),
            filename=PurePosixPath(value.name).name,
            media_type=value.media_type if inline else "application/octet-stream",
            content_disposition_type="inline" if inline else "attachment",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/{identity}/text")
    def text(workspace_id: str, identity: str) -> OutputText:
        outputs = Outputs(workspace())
        value = outputs.get(workspace_id, identity)
        limit = 64 * 1024
        with outputs.content(value).open("rb") as stream:
            raw = stream.read(limit + 1)
        truncated = len(raw) > limit
        try:
            decoded = codecs.getincrementaldecoder("utf-8-sig")().decode(
                raw[:limit], final=not truncated
            )
            if any(ord(char) < 32 and char not in "\t\r\n" for char in decoded):
                raise ValueError("Binary content")
        except (UnicodeDecodeError, ValueError):
            return OutputText(text="", supported=False, truncated=False)
        return OutputText(text=decoded, supported=True, truncated=truncated)

    return router
