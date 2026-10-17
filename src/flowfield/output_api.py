"""Workspace-scoped metadata and retained file downloads."""

from collections.abc import Callable
from pathlib import PurePosixPath

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from flowfield.application import Workspace
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
