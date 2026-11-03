"""Service-host harness settings; GET detects paths without running native commands."""

from collections.abc import Callable

from fastapi import APIRouter

from flowfield.agent_configuration import check_harness, harness_status
from flowfield.application import Workspace
from flowfield.harness_models import (
    CatalogConfirmation,
    HarnessEdit,
    HarnessKind,
    HarnessRegistration,
    HarnessStatus,
)
from flowfield.harness_settings import HarnessSettings
from flowfield.supervisor import Supervisor


def harness_router(
    workspace: Callable[[], Workspace], supervisor: Callable[[], Supervisor]
) -> APIRouter:
    router = APIRouter(prefix="/api/harnesses")

    @router.get("")
    def harnesses() -> list[HarnessStatus]:
        service = workspace()
        return [
            harness_status(supervisor(), item.harness) for item in HarnessSettings(service).all()
        ]

    @router.get("/{harness}")
    def harness(harness: HarnessKind) -> HarnessStatus:
        return harness_status(supervisor(), harness)

    @router.put("/{harness}")
    def edit(harness: HarnessKind, request: HarnessEdit) -> HarnessRegistration:
        return HarnessSettings(workspace()).edit(harness, request)

    @router.post("/{harness}/check")
    async def native_check(harness: HarnessKind) -> HarnessStatus:
        return await check_harness(supervisor(), harness)

    @router.post("/{harness}/catalog/confirm-stopped")
    def confirm_stopped(harness: HarnessKind, request: CatalogConfirmation) -> HarnessStatus:
        supervisor().catalogs.confirm_stopped(harness, request.id)
        return harness_status(supervisor(), harness)

    return router
