"""Service-owned integration settings and browser-readable evidence."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from flowfield.adapters.git_review import changed_files, file_patch
from flowfield.check_logs import download
from flowfield.errors import ApplicationError
from flowfield.integration_models import (
    IntegrationConfig,
    IntegrationPage,
    IntegrationRead,
    IntegrationSettings,
)
from flowfield.review_models import ChangedFiles, FilePatch
from flowfield.setup_validation import SetupCheck, SetupCheckRequest
from flowfield.supervisor import Supervisor


def integration_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/projects/{project_id}/integration")
    def settings(project_id: str) -> IntegrationSettings:
        return supervisor().integrations.settings(project_id)

    @router.put("/projects/{project_id}/integration")
    def configure(project_id: str, request: IntegrationConfig) -> IntegrationSettings:
        return supervisor().integrations.configure(project_id, request)

    @router.get("/projects/{project_id}/setup-validation")
    def setup_validation(project_id: str, check_id: str | None = None) -> SetupCheck | None:
        return supervisor().setup_validation.get(project_id, check_id)

    @router.post("/projects/{project_id}/setup-validation")
    async def validate_setup(project_id: str, request: SetupCheckRequest) -> SetupCheck:
        return await supervisor().setup_validation.check(project_id, request)

    @router.get("/workspaces/{workspace_id}/integrations")
    def listing(
        workspace_id: str,
        run_id: str | None = None,
        before: int | None = None,
        limit: int = Query(default=10, ge=1, le=50),
    ) -> IntegrationPage:
        return supervisor().integrations.page(workspace_id, run_id, before, limit)

    @router.get("/workspaces/{workspace_id}/integrations/{identity}")
    def detail(workspace_id: str, identity: str) -> IntegrationRead:
        return supervisor().integrations.read(workspace_id, identity)

    @router.get("/workspaces/{workspace_id}/integrations/{identity}/logs/{phase}/{index}")
    def integration_log(
        workspace_id: str, identity: str, phase: Literal["setup", "checks"], index: int
    ) -> FileResponse:
        value = supervisor().integrations.read(workspace_id, identity)
        if not value.workspace:
            raise ApplicationError("not_found", "No command environment retained.", 404)
        checks = value.setup_checks if phase == "setup" else value.checks
        if value.progress:
            checks = value.progress.setup_checks if phase == "setup" else value.progress.checks
        return download(Path(value.workspace).parent / "runtime", checks, index)

    @router.get("/projects/{project_id}/setup-validation/{identity}/logs/{phase}/{index}")
    def setup_log(
        project_id: str, identity: str, phase: Literal["setup", "checks"], index: int
    ) -> FileResponse:
        value = supervisor().setup_validation.get(project_id, identity)
        if not value or not value.workspace:
            raise ApplicationError("not_found", "No command environment retained.", 404)
        return download(
            Path(value.workspace).parent / "runtime",
            value.setup if phase == "setup" else value.checks,
            index,
        )

    def comparison(workspace_id: str, identity: str) -> tuple[Path, str, str]:
        record = supervisor().integrations.get(workspace_id, identity)
        if not record.candidate_commit:
            raise ApplicationError(
                "candidate_missing", "This integration has no combined candidate.", 409
            )
        return (
            Path(supervisor().workspace.project(record.project_id).path),
            record.target_before,
            record.candidate_commit,
        )

    @router.get("/workspaces/{workspace_id}/integrations/{identity}/diff")
    async def diff(
        workspace_id: str,
        identity: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> ChangedFiles:
        return await asyncio.to_thread(
            changed_files, *comparison(workspace_id, identity), offset, limit
        )

    @router.get("/workspaces/{workspace_id}/integrations/{identity}/diff/{file_id}")
    async def patch(workspace_id: str, identity: str, file_id: int) -> FilePatch:
        return await asyncio.to_thread(file_patch, *comparison(workspace_id, identity), file_id)

    return router
