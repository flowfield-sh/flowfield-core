"""Workspace-scoped metadata and retained file downloads."""

from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from flowfield.application import Workspace
from flowfield.file_browser import (
    FileBrowser,
    FileDirectory,
    FileEntry,
    FileFolder,
    FileLocation,
    FileWorkPage,
)
from flowfield.output_models import OutputFile, OutputPage
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
        milestone_id: str | None = None,
    ) -> OutputPage:
        return Outputs(workspace()).page(
            workspace_id,
            before=before,
            limit=limit,
            task_key=task_key,
            milestone_id=milestone_id,
        )

    @router.get("/browse")
    def browse(
        workspace_id: str,
        path: str = Query("", max_length=500),
        task_key: str | None = None,
        milestone_id: str | None = None,
        sort: Literal["name", "newest", "type"] = "name",
        offset: int = Query(0, ge=0),
    ) -> FileDirectory:
        return FileBrowser(workspace()).directory(
            workspace_id,
            path,
            task_key=task_key,
            milestone_id=milestone_id,
            sort=sort,
            offset=offset,
        )

    @router.get("/work")
    def work(workspace_id: str, offset: int = Query(0, ge=0)) -> FileWorkPage:
        return FileBrowser(workspace()).work(workspace_id, offset)

    @router.post("/folders")
    def folder(workspace_id: str, value: FileFolder) -> FileFolder:
        return FileBrowser(workspace()).folder(workspace_id, value)

    @router.put("/{identity}/location")
    def move(workspace_id: str, identity: str, value: FileLocation) -> FileEntry:
        return FileBrowser(workspace()).move(workspace_id, identity, value)

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

    return router
