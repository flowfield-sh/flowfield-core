"""Managed-worker HTTP operations shared by the board and coordinator."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Query

from flowfield.adapters.git_review import changed_files, file_patch
from flowfield.errors import ApplicationError
from flowfield.execution_models import (
    ModelOption,
    QueueEdit,
    Run,
    RunAction,
    RunLocation,
    RunPage,
    SettingsEdit,
    WorkerOccupancy,
    WorkerSettings,
)
from flowfield.harness_models import HarnessKind
from flowfield.review_models import ChangedFiles, FilePatch
from flowfield.run_activity import RunActivity, RunActivityPage
from flowfield.supervisor import Supervisor


def execution_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api")

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

    @router.get("/projects/{project_id}/workers")
    def settings(project_id: str) -> WorkerSettings:
        return supervisor().execution.settings(project_id)

    @router.put("/projects/{project_id}/workers")
    async def configure(project_id: str, request: SettingsEdit) -> WorkerSettings:
        return await supervisor().configure(project_id, request)

    @router.get("/projects/{project_id}/workers/occupancy")
    def occupancy(project_id: str) -> WorkerOccupancy:
        return supervisor().execution.occupancy(project_id)

    @router.post("/projects/{project_id}/queue")
    def queue(project_id: str, request: QueueEdit) -> WorkerSettings:
        return supervisor().execution.queue(project_id, request)

    @router.get("/projects/{project_id}/runs")
    def runs(
        project_id: str,
        task_id: str | None = None,
        before: int | None = None,
        limit: int = 20,
        attention: bool = False,
    ) -> RunPage:
        return supervisor().execution.page(
            project_id, task_id=task_id, before=before, limit=limit, attention=attention
        )

    @router.get("/projects/{project_id}/runs/{run_id}")
    def run(project_id: str, run_id: str) -> Run:
        return supervisor().execution.get(project_id, run_id)

    @router.get("/projects/{project_id}/runs/{run_id}/location")
    def location(project_id: str, run_id: str) -> RunLocation:
        return supervisor().location(project_id, run_id)

    @router.get("/projects/{project_id}/runs/{run_id}/activity")
    def activity(project_id: str, run_id: str, after: int = Query(-1, ge=-1)) -> RunActivityPage:
        return RunActivity(supervisor().workspace).read(project_id, run_id, after)

    def comparison(project_id: str, run_id: str) -> tuple[Path, str, str]:
        service = supervisor()
        record = service.execution.get(project_id, run_id)
        environment = service.environment(run_id)
        if not environment or not record.result_commit:
            raise ApplicationError(
                "result_missing", "This attempt has no captured code result yet.", 409
            )
        return environment.checkout, record.base_commit, record.result_commit

    @router.get("/projects/{project_id}/runs/{run_id}/diff")
    async def diff(
        project_id: str,
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> ChangedFiles:
        return await asyncio.to_thread(
            changed_files, *comparison(project_id, run_id), offset, limit
        )

    @router.get("/projects/{project_id}/runs/{run_id}/diff/{file_id}")
    async def patch(project_id: str, run_id: str, file_id: int) -> FilePatch:
        return await asyncio.to_thread(file_patch, *comparison(project_id, run_id), file_id)

    @router.post("/projects/{project_id}/runs/{run_id}/stop")
    async def stop(project_id: str, run_id: str, request: RunAction) -> Run:
        return await supervisor().stop(project_id, run_id, request)

    @router.post("/projects/{project_id}/runs/{run_id}/retry")
    def retry(project_id: str, run_id: str, request: RunAction) -> Run:
        return supervisor().execution.retry(project_id, run_id, request)

    return router
