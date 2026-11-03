"""External-agent control of the built-in Coordinator; never supplied to itself."""

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from flowfield.coordinator_models import CoordinatorSend
from flowfield.supervisor import Supervisor


def add_coordinator_control_tools(mcp: FastMCP, supervisor: Callable[[], Supervisor]) -> None:
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=write)
    async def send_coordinator_message(
        workspace_id: str, request: CoordinatorSend
    ) -> dict[str, Any]:
        """Send the user's message to the built-in Coordinator, starting a model turn.
        Requires explicit intent to run that Coordinator. Read get_coordinator_history
        for its response. Not available inside the built-in Coordinator itself.
        """
        service = supervisor()
        conversation = service.coordinator.store.new(workspace_id)
        return service.coordinator.send(workspace_id, conversation.id, request).model_dump()

    @mcp.tool(annotations=write)
    async def stop_coordinator(workspace_id: str, turn_id: str) -> dict[str, Any]:
        """Stop an exact built-in Coordinator turn when the human asks; preserve chat."""
        return (await supervisor().coordinator.stop(workspace_id, turn_id)).model_dump()

    @mcp.tool(annotations=write)
    def confirm_coordinator_stopped(workspace_id: str, turn_id: str) -> dict[str, Any]:
        """Relay human confirmation that uncertain Coordinator work was stopped on the
        service host. Never infer cleanup from elapsed time or a lost connection.
        """
        return supervisor().coordinator.store.confirm_stopped(workspace_id, turn_id).model_dump()

    @mcp.tool(annotations=write)
    def reset_coordinator_session(workspace_id: str, turn_id: str) -> dict[str, Any]:
        """Reset the stopped native session when requested; retain saved chat and workspace.
        The next message starts a fresh native session. Never bypass uncertain cleanup.
        """
        supervisor().coordinator.store.reset_session(workspace_id, turn_id)
        return {"saved": True, "turn_id": turn_id}

    @mcp.tool(annotations=write)
    async def discover_coordinator_commands(
        workspace_id: str, refresh: bool = False
    ) -> list[dict[str, Any]]:
        """Discover the selected Coordinator harness's supported slash commands.
        May start native discovery and startup hooks; sends no model prompt.
        """
        return [
            item.model_dump()
            for item in await supervisor().coordinator.commands(workspace_id, refresh=refresh)
        ]
