"""Translate only offered native choices; policy and option meanings remain harness-owned."""

from flowfield.adapters.agent_contract import PermissionHandler, PermissionRequest
from flowfield.permission_models import PermissionOption
from flowfield.permissions import PermissionTurn


def permission_handler(turn: PermissionTurn) -> PermissionHandler:
    async def request(value: PermissionRequest) -> str | None:
        options = [
            PermissionOption.model_validate({"id": identity, "label": label, "kind": kind})
            for identity, label, kind in value.options
        ]
        return await turn.request(value.tool_id, value.title, options, details=value.details)

    return request
