"""Human-facing shared settings and permission answers; no request-creation endpoint."""

from collections.abc import Callable

from fastapi import APIRouter, Query

from flowfield.agent_configuration import configure_worker
from flowfield.agent_models import AgentRole, AgentSettingsEdit, AgentSettingsView
from flowfield.agent_settings import AgentSettings
from flowfield.permission_models import PermissionAnswer, PermissionPage, PermissionRecord
from flowfield.supervisor import Supervisor


def agent_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}")

    @router.get("/tasks/{task_id}/agent-settings")
    def task_settings(workspace_id: str, task_id: str) -> AgentSettingsView:
        return AgentSettings(supervisor().workspace).get(workspace_id, "worker", task_id)

    @router.put("/tasks/{task_id}/agent-settings")
    async def task_edit(
        workspace_id: str, task_id: str, request: AgentSettingsEdit
    ) -> AgentSettingsView:
        return await configure_worker(supervisor(), workspace_id, task_id, request)

    @router.get("/permissions")
    def permissions(
        workspace_id: str,
        task_id: str | None = None,
        role: AgentRole | None = None,
        before: int | None = Query(None, ge=1),
        limit: int = Query(50, ge=1, le=100),
    ) -> PermissionPage:
        return supervisor().permissions.page(
            workspace_id, task_id=task_id, role=role, before=before, limit=limit
        )

    @router.post("/permissions/{permission_id}/answer")
    async def answer(
        workspace_id: str, permission_id: str, request: PermissionAnswer
    ) -> PermissionRecord:
        # Delivery and live bindings are event-loop owned; do not run this in a thread pool.
        return supervisor().permissions.answer(workspace_id, permission_id, request)

    @router.get("/permissions/{permission_id}")
    def permission(workspace_id: str, permission_id: str) -> PermissionRecord:
        return supervisor().permissions.get(workspace_id, permission_id)

    return router
