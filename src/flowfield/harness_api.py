"""Service-host harness settings; GET detects paths without running native commands."""

import os
from collections.abc import Callable

from fastapi import APIRouter

from flowfield.adapters.harness_host import status
from flowfield.application import Workspace
from flowfield.harness_models import HarnessEdit, HarnessKind, HarnessRegistration, HarnessStatus
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
            status(service.directory, item, os.environ) for item in HarnessSettings(service).all()
        ]

    @router.get("/{harness}")
    def harness(harness: HarnessKind) -> HarnessStatus:
        service = workspace()
        return status(service.directory, HarnessSettings(service).get(harness), os.environ)

    @router.put("/{harness}")
    def edit(harness: HarnessKind, request: HarnessEdit) -> HarnessRegistration:
        return HarnessSettings(workspace()).edit(harness, request)

    @router.post("/{harness}/check")
    async def native_check(harness: HarnessKind) -> HarnessStatus:
        service = workspace()
        return await supervisor().harness_checks.run(
            HarnessSettings(service).get(harness), os.environ
        )

    return router
