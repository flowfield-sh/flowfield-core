"""Human-facing shared settings and permission answers; no request-creation endpoint."""

from collections.abc import Callable

from fastapi import APIRouter, Query

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
        service = supervisor()
        if request.selection:
            task = service.workspace.task(workspace_id, task_id)
            await service.validate_agent_choice(request.selection, task.project_id)
        return AgentSettings(service.workspace).edit(workspace_id, "worker", request, task_id)

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
        service = supervisor()
        with service.workspace.connection() as db:
            return service.permissions._get(db, workspace_id, permission_id)

    return router
