"""Human-facing shared settings and permission answers; no request-creation endpoint."""

from collections.abc import Callable

from fastapi import APIRouter, Query

from flowfield.agent_models import AgentRole, AgentSettingsEdit, AgentSettingsView
from flowfield.agent_settings import AgentSettings
from flowfield.permission_models import PermissionAnswer, PermissionPage, PermissionRecord
from flowfield.supervisor import Supervisor


def agent_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}")

    @router.get("/coordinator-settings")
    def coordinator_settings(project_id: str) -> AgentSettingsView:
        return AgentSettings(supervisor().workspace).get(project_id, "coordinator")

    @router.put("/coordinator-settings")
    async def coordinator_edit(project_id: str, request: AgentSettingsEdit) -> AgentSettingsView:
        service = supervisor()
        if request.selection:
            await service.validate_agent_choice(request.selection, project_id)
        return AgentSettings(service.workspace).edit(project_id, "coordinator", request)

    @router.get("/tasks/{task_id}/agent-settings")
    def task_settings(project_id: str, task_id: str) -> AgentSettingsView:
        return AgentSettings(supervisor().workspace).get(project_id, "worker", task_id)

    @router.put("/tasks/{task_id}/agent-settings")
    async def task_edit(
        project_id: str, task_id: str, request: AgentSettingsEdit
    ) -> AgentSettingsView:
        service = supervisor()
        if request.selection:
            await service.validate_agent_choice(request.selection, project_id)
        return AgentSettings(service.workspace).edit(project_id, "worker", request, task_id)

    @router.get("/permissions")
    def permissions(
        project_id: str,
        task_id: str | None = None,
        role: AgentRole | None = None,
        before: int | None = Query(None, ge=1),
        limit: int = Query(50, ge=1, le=100),
    ) -> PermissionPage:
        return supervisor().permissions.page(
            project_id, task_id=task_id, role=role, before=before, limit=limit
        )

    @router.post("/permissions/{permission_id}/answer")
    async def answer(
        project_id: str, permission_id: str, request: PermissionAnswer
    ) -> PermissionRecord:
        # Delivery and live bindings are event-loop owned; do not run this in a thread pool.
        return supervisor().permissions.answer(project_id, permission_id, request)

    @router.get("/permissions/{permission_id}")
    def permission(project_id: str, permission_id: str) -> PermissionRecord:
        service = supervisor()
        with service.workspace.connection() as db:
            return service.permissions._get(db, project_id, permission_id)

    return router
