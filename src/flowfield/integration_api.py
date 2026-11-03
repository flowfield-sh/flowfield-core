"""Service-owned integration settings and browser-readable evidence."""

import asyncio
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from flowfield.adapters.git_review import changed_files, file_patch
from flowfield.check_logs import download
from flowfield.execution_evidence import command_receipts, comparison, setup_receipts
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
        return download(
            *command_receipts(supervisor(), workspace_id, "integration", identity, phase), index
        )

    @router.get("/projects/{project_id}/setup-validation/{identity}/logs/{phase}/{index}")
    def setup_log(
        project_id: str, identity: str, phase: Literal["setup", "checks"], index: int
    ) -> FileResponse:
        return download(*setup_receipts(supervisor(), project_id, identity, phase), index)

    @router.get("/workspaces/{workspace_id}/integrations/{identity}/diff")
    async def diff(
        workspace_id: str,
        identity: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> ChangedFiles:
        return await asyncio.to_thread(
            changed_files,
            *comparison(supervisor(), workspace_id, "integration", identity),
            offset,
            limit,
        )

    @router.get("/workspaces/{workspace_id}/integrations/{identity}/diff/{file_id}")
    async def patch(workspace_id: str, identity: str, file_id: int) -> FilePatch:
        return await asyncio.to_thread(
            file_patch, *comparison(supervisor(), workspace_id, "integration", identity), file_id
        )

    return router
