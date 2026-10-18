"""Stable task result and exact-version review over HTTP."""

import asyncio
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Query

from flowfield.execution_history import (
    ExecutionHistory,
    ExecutionItem,
    ExecutionPage,
    TaskHistoryPage,
)
from flowfield.execution_models import RunAction
from flowfield.result_models import ResultPage, ResultReview, ResultVersion
from flowfield.supervisor import Supervisor


def result_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}")

    @router.get("/tasks/{task_id}/history")
    def history(
        workspace_id: str,
        task_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=30, ge=1, le=50),
        mode: Literal["all", "notes", "attempts"] = "all",
    ) -> TaskHistoryPage:
        return ExecutionHistory(supervisor().workspace).timeline(
            workspace_id, task_id, offset, limit, mode
        )

    @router.get("/tasks/{task_id}/executions")
    def executions(
        workspace_id: str,
        task_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
        run_id: str | None = None,
    ) -> ExecutionPage:
        return ExecutionHistory(supervisor().workspace).page(
            workspace_id, task_id, offset, limit, run_id
        )

    @router.get("/tasks/{task_id}/executions/{identity}")
    def execution(workspace_id: str, task_id: str, identity: str) -> ExecutionItem:
        return ExecutionHistory(supervisor().workspace).get(workspace_id, task_id, identity)

    @router.get("/tasks/{task_id}/results")
    def listing(
        workspace_id: str,
        task_id: str,
        before: int | None = None,
        limit: int = Query(default=10, ge=1, le=50),
    ) -> ResultPage:
        return supervisor().results.page(workspace_id, task_id, before, limit)

    @router.get("/results/{identity}")
    def detail(workspace_id: str, identity: str) -> ResultVersion:
        return supervisor().results.get(workspace_id, identity)

    @router.post("/results/{identity}/review")
    def review(workspace_id: str, identity: str, request: ResultReview) -> ResultVersion:
        return supervisor().results.review(workspace_id, identity, request)

    @router.post("/results/{identity}/prepare")
    def prepare(workspace_id: str, identity: str, request: RunAction) -> ResultVersion:
        return supervisor().results.reprepare(workspace_id, identity, request)

    @router.post("/results/{identity}/cancel")
    def cancel(workspace_id: str, identity: str, request: RunAction) -> ResultVersion:
        return supervisor().results.cancel(workspace_id, identity, request)

    @router.post("/results/{identity}/retry-delivery")
    def retry_delivery(workspace_id: str, identity: str, request: RunAction) -> ResultVersion:
        return supervisor().results.retry_delivery(workspace_id, identity, request)

    @router.post("/results/{identity}/correct")
    async def correct(workspace_id: str, identity: str, request: RunAction) -> ResultVersion:
        return await asyncio.to_thread(
            supervisor().results.correct, workspace_id, identity, request
        )

    @router.post("/results/{identity}/revalidate")
    async def revalidate(workspace_id: str, identity: str, request: RunAction) -> ResultVersion:
        return await asyncio.to_thread(
            supervisor().results.revalidate, workspace_id, identity, request
        )

    return router
