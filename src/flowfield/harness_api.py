"""Service-host harness settings; GET detects paths without running native commands."""

import os
from collections.abc import Callable

from fastapi import APIRouter

from flowfield.adapters.harness_host import status
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
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

    def owned(value: HarnessStatus) -> HarnessStatus:
        value = value.model_copy(deep=True)
        value.catalog_ownership = supervisor().catalogs.ownership(value.registration.harness)
        if value.catalog_ownership:
            value.problems.append("catalog_" + value.catalog_ownership.status)
            value.selectable = False
        return value

    def view(item: HarnessRegistration) -> HarnessStatus:
        return owned(status(workspace().directory, item, os.environ))

    @router.get("")
    def harnesses() -> list[HarnessStatus]:
        service = workspace()
        return [view(item) for item in HarnessSettings(service).all()]

    @router.get("/{harness}")
    def harness(harness: HarnessKind) -> HarnessStatus:
        service = workspace()
        return view(HarnessSettings(service).get(harness))

    @router.put("/{harness}")
    def edit(harness: HarnessKind, request: HarnessEdit) -> HarnessRegistration:
        return HarnessSettings(workspace()).edit(harness, request)

    @router.post("/{harness}/check")
    async def native_check(harness: HarnessKind) -> HarnessStatus:
        service = workspace()
        registration = HarnessSettings(service).get(harness)
        checked = await supervisor().harness_checks.run(registration, os.environ)
        if HarnessSettings(service).get(harness) != registration:
            raise ApplicationError(
                "harness_changed",
                "Host settings changed during the check. Reload and check again.",
                409,
            )
        return owned(checked)

    @router.post("/{harness}/catalog/confirm-stopped")
    def confirm_stopped(harness: HarnessKind, request: CatalogConfirmation) -> HarnessStatus:
        supervisor().catalogs.confirm_stopped(harness, request.id)
        return view(HarnessSettings(workspace()).get(harness))

    return router
