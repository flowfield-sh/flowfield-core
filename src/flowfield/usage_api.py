"""Human read-only usage routes under the existing local origin/access boundary."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from flowfield.application import Workspace
from flowfield.usage_models import Source, UsageEvidencePage, UsageFilter, UsageView
from flowfield.usage_reads import UsageReads


def usage_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces")

    @router.get("/{workspace_id}/usage")
    def usage(workspace_id: str, filters: Annotated[UsageFilter, Query()]) -> UsageView:
        if filters.start is None:
            end = datetime.now(UTC)
            filters = filters.model_copy(update={"start": end - timedelta(days=7), "end": end})
        return UsageReads(workspace()).view(workspace_id, filters)

    @router.get("/{workspace_id}/tasks/{task_id}/usage")
    def task_usage(
        workspace_id: str, task_id: str, filters: Annotated[UsageFilter, Query()]
    ) -> UsageView:
        return UsageReads(workspace()).view(workspace_id, filters, task=task_id)

    @router.get("/{workspace_id}/usage/{source}/{source_id}")
    def evidence(
        workspace_id: str,
        source: Source,
        source_id: str,
        after: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
    ) -> UsageEvidencePage:
        return UsageReads(workspace()).evidence(workspace_id, source, source_id, after, limit)

    return router
