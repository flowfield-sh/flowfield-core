"""Browser chat operations. Reading a project never starts a model turn."""

from collections.abc import Callable

from fastapi import APIRouter, Query

from flowfield.agent_models import AgentCommand, AgentSettingsEdit, AgentSettingsView
from flowfield.coordinator_models import (
    CoordinatorConversation,
    CoordinatorPage,
    CoordinatorSend,
    CoordinatorTurn,
)
from flowfield.coordinator_settings import CoordinatorSettings
from flowfield.supervisor import Supervisor


def coordinator_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/coordinator")

    @router.get("")
    def history(
        workspace_id: str,
        before: int | None = Query(None, ge=1),
        after: int | None = Query(
            None,
            ge=1,
            description="Refresh this turn and newer turns, up to 20 in ascending order.",
        ),
    ) -> CoordinatorPage:
        return supervisor().coordinator.store.page(workspace_id, before=before, after=after)

    @router.post("", status_code=201)
    def new(workspace_id: str) -> CoordinatorConversation:
        return supervisor().coordinator.store.new(workspace_id)

    @router.post("/commands/discover")
    async def commands(workspace_id: str, refresh: bool = False) -> list[AgentCommand]:
        return await supervisor().coordinator.commands(workspace_id, refresh=refresh)

    @router.post("/messages", status_code=202)
    async def message(workspace_id: str, request: CoordinatorSend) -> CoordinatorTurn:
        service = supervisor()
        conversation = service.coordinator.store.new(workspace_id)
        return service.coordinator.send(workspace_id, conversation.id, request)

    @router.get("/{conversation_id}")
    def page(
        workspace_id: str, conversation_id: str, before: int | None = Query(None, ge=1)
    ) -> CoordinatorPage:
        return supervisor().coordinator.store.page(workspace_id, conversation_id, before)

    @router.post("/{conversation_id}/messages", status_code=202)
    async def send(
        workspace_id: str, conversation_id: str, request: CoordinatorSend
    ) -> CoordinatorTurn:
        return supervisor().coordinator.send(workspace_id, conversation_id, request)

    @router.post("/turns/{turn_id}/stop")
    async def stop(workspace_id: str, turn_id: str) -> CoordinatorTurn:
        return await supervisor().coordinator.stop(workspace_id, turn_id)

    @router.post("/turns/{turn_id}/confirm-stopped")
    def recover(workspace_id: str, turn_id: str) -> CoordinatorTurn:
        return supervisor().coordinator.store.confirm_stopped(workspace_id, turn_id)

    @router.post("/turns/{turn_id}/reset-session", status_code=204)
    def reset_session(workspace_id: str, turn_id: str) -> None:
        supervisor().coordinator.store.reset_session(workspace_id, turn_id)

    @router.get("/{conversation_id}/settings")
    def settings(workspace_id: str, conversation_id: str) -> AgentSettingsView:
        supervisor().coordinator.store.page(workspace_id, conversation_id)
        return CoordinatorSettings(supervisor().workspace).get(workspace_id)

    @router.put("/{conversation_id}/settings")
    async def edit(
        workspace_id: str, conversation_id: str, request: AgentSettingsEdit
    ) -> AgentSettingsView:
        service = supervisor()
        service.coordinator.store.page(workspace_id, conversation_id)
        return await service.configure_workspace_coordinator(workspace_id, request)

    return router
