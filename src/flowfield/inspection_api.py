"""Explicit inspection preparation and read-only saved copies."""

from collections.abc import Callable

from fastapi import APIRouter

from flowfield.application import Workspace
from flowfield.inspection import Inspections
from flowfield.inspection_models import (
    Inspection,
    InspectionConfig,
    InspectionPrepare,
    InspectionSettings,
)


def inspection_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/projects/{project_id}/inspection/settings")
    def settings(project_id: str) -> InspectionSettings:
        return Inspections(workspace()).settings(project_id)

    @router.put("/projects/{project_id}/inspection/settings")
    def configure(project_id: str, request: InspectionConfig) -> InspectionSettings:
        return Inspections(workspace()).configure(project_id, request)

    @router.get("/workspaces/{workspace_id}/inspection")
    def latest(workspace_id: str, result_id: str) -> Inspection | None:
        return Inspections(workspace()).latest(workspace_id, result_id)

    @router.get("/workspaces/{workspace_id}/inspections/{identity}")
    def detail(workspace_id: str, identity: str) -> Inspection:
        return Inspections(workspace()).get(workspace_id, identity)

    @router.post("/workspaces/{workspace_id}/inspection")
    def prepare(workspace_id: str, request: InspectionPrepare) -> Inspection:
        return Inspections(workspace()).prepare(workspace_id, request)

    return router
