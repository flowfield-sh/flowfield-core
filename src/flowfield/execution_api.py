"""Managed-worker HTTP operations shared by the board and coordinator."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Query

from flowfield.adapters.git_review import changed_files, file_patch
from flowfield.errors import ApplicationError
from flowfield.execution_models import (
    ModelOption,
    Run,
    RunAction,
    RunLocation,
    RunPage,
)
from flowfield.harness_models import HarnessKind
from flowfield.review_models import ChangedFiles, FilePatch
from flowfield.run_activity import RunActivity, RunActivityPage
from flowfield.supervisor import Supervisor
from flowfield.worker_checks import CheckPermission, CheckPermissionEdit, CheckPermissions


def execution_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/projects/{project_id}/check-permission")
    def check_permission(project_id: str) -> CheckPermission:
        return CheckPermissions(supervisor().workspace).get(project_id)

    @router.put("/projects/{project_id}/check-permission")
    def configure_check_permission(
        project_id: str, request: CheckPermissionEdit
    ) -> CheckPermission:
        return CheckPermissions(supervisor().workspace).configure(project_id, request)

    @router.get("/worker-models")
    async def models(
        refresh: bool = False,
        harness: HarnessKind = "codex",
        project_id: str | None = None,
        project_path: str | None = None,
    ) -> list[ModelOption]:
        return await supervisor().model_options(
            refresh=refresh, harness=harness, project_id=project_id, project_path=project_path
        )

    @router.get("/workspaces/{workspace_id}/runs")
    def runs(
        workspace_id: str,
        task_id: str | None = None,
        before: int | None = None,
        limit: int = 20,
        attention: bool = False,
    ) -> RunPage:
        return supervisor().execution.page(
            workspace_id, task_id=task_id, before=before, limit=limit, attention=attention
        )

    @router.post("/workspaces/{workspace_id}/tasks/{task_id}/stop")
    async def stop_task(workspace_id: str, task_id: str, request: RunAction) -> list[Run]:
        return await supervisor().stop_task(workspace_id, task_id, request)

    @router.get("/workspaces/{workspace_id}/runs/{run_id}")
    def run(workspace_id: str, run_id: str) -> Run:
        return supervisor().execution.get(workspace_id, run_id)

    @router.get("/workspaces/{workspace_id}/runs/{run_id}/location")
    def location(workspace_id: str, run_id: str) -> RunLocation:
        return supervisor().location(workspace_id, run_id)

    @router.get("/workspaces/{workspace_id}/runs/{run_id}/activity")
    def activity(workspace_id: str, run_id: str, after: int = Query(-1, ge=-1)) -> RunActivityPage:
        return RunActivity(supervisor().workspace).read(workspace_id, run_id, after)

    def comparison(workspace_id: str, run_id: str) -> tuple[Path, str, str]:
        service = supervisor()
        record = service.execution.get(workspace_id, run_id)
        environment = service.environment(run_id)
        if not environment or not record.result_commit:
            raise ApplicationError(
                "result_missing", "This attempt has no captured code result yet.", 409
            )
        return environment.checkout, record.base_commit, record.result_commit

    @router.get("/workspaces/{workspace_id}/runs/{run_id}/diff")
    async def diff(
        workspace_id: str,
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> ChangedFiles:
        return await asyncio.to_thread(
            changed_files, *comparison(workspace_id, run_id), offset, limit
        )

    @router.get("/workspaces/{workspace_id}/runs/{run_id}/diff/{file_id}")
    async def patch(workspace_id: str, run_id: str, file_id: int) -> FilePatch:
        return await asyncio.to_thread(file_patch, *comparison(workspace_id, run_id), file_id)

    @router.post("/workspaces/{workspace_id}/runs/{run_id}/stop")
    async def stop(workspace_id: str, run_id: str, request: RunAction) -> Run:
        return await supervisor().stop(workspace_id, run_id, request)

    @router.post("/workspaces/{workspace_id}/runs/{run_id}/retry")
    def retry(workspace_id: str, run_id: str, request: RunAction) -> Run:
        return supervisor().execution.retry(workspace_id, run_id, request)

    return router
