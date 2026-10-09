"""Candidate Claude bridge receipt validation, shared ACP lifecycle callback.

This does not enable Claude launch or establish native ownership compatibility.
The extension currently exists only in an explicitly opted-in development proof.
"""

from typing import Any, Literal

from acp.client import ClientSideConnection
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flowfield.adapters.acp_session import ShutdownTimeouts
from flowfield.errors import ApplicationError

CLAUDE_SHUTDOWN_TIMEOUTS = ShutdownTimeouts(native_cleanup=15)
CAPABILITY = {
    "version": 1,
    "method": "_flowfield/quiesce",
    "scope": "native-turns-and-tasks",
}


class CleanupReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    method: Literal["_flowfield/quiesce"]
    scope: Literal["native-turns-and-tasks"]
    sessionId: str
    status: Literal["confirmed", "uncertain"]
    reason: str | None
    checkedNativeOwners: int = Field(ge=0, le=1)
    stoppedTasks: int = Field(ge=0, le=256)
    quietObservations: int = Field(ge=0, le=100)
    nativeOwnerExited: bool


def require_cleanup(capabilities: dict[str, Any]) -> None:
    metadata = capabilities.get("_meta")
    value = metadata.get("flowfield.cleanup") if isinstance(metadata, dict) else None
    if value != CAPABILITY or type(value.get("version")) is not int:
        raise ApplicationError(
            "cleanup_unavailable",
            "This Claude bridge does not support the required native cleanup receipt.",
            409,
        )


async def quiesce(
    connection: ClientSideConnection, session_id: str, capabilities: dict[str, Any]
) -> bool:
    require_cleanup(capabilities)
    response = await connection.ext_method("flowfield/quiesce", {"sessionId": session_id})
    if not isinstance(response, dict) or type(response.get("version")) is not int:
        return False
    try:
        receipt = CleanupReceipt.model_validate(response)
    except ValidationError:
        return False
    return (
        receipt.sessionId == session_id
        and receipt.status == "confirmed"
        and receipt.reason is None
        and receipt.checkedNativeOwners == 1
        and receipt.quietObservations >= 2
        and receipt.nativeOwnerExited
    )
