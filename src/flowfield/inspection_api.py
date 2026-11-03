"""Explicit inspection preparation and read-only saved copies."""

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import FileResponse

from flowfield.application import Workspace
from flowfield.inspection import Inspections
from flowfield.inspection_models import (
    Inspection,
    InspectionConfig,
    InspectionPrepare,
    InspectionRuntimeAction,
    InspectionSettings,
)
from flowfield.supervisor import Supervisor


def inspection_router(
    workspace: Callable[[], Workspace], supervisor: Callable[[], Supervisor]
) -> APIRouter:
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

    @router.post("/workspaces/{workspace_id}/inspection/runtime")
    async def prepare_runtime(workspace_id: str, request: InspectionPrepare) -> Inspection:
        return await supervisor().result_runtimes.prepare(workspace_id, request)

    @router.get("/workspaces/{workspace_id}/inspections/{identity}/runtime/log")
    def runtime_log(workspace_id: str, identity: str) -> FileResponse:
        from pathlib import Path

        from flowfield.errors import ApplicationError

        value = Inspections(workspace()).get(workspace_id, identity)
        if not value.workspace or not value.runtime_generation:
            raise ApplicationError("not_found", "No runtime log is available.", 404)
        path = Path(value.workspace).parent / "runtime" / f"runtime-{value.runtime_generation}.log"
        root = workspace().directory / "inspection-work"
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ApplicationError("not_found", "Runtime log is unavailable.", 404)
        return FileResponse(path, media_type="text/plain", filename="result-runtime.log")

    @router.post("/workspaces/{workspace_id}/inspections/{identity}/runtime")
    async def runtime(
        workspace_id: str, identity: str, request: InspectionRuntimeAction
    ) -> Inspection:
        runtimes = supervisor().result_runtimes
        if request.action == "confirm-stopped":
            return await runtimes.confirm_stopped(workspace_id, identity)
        if request.action == "stop":
            return await runtimes.stop(workspace_id, identity)
        if request.action == "run":
            return await runtimes.run(workspace_id, identity)
        value = Inspections(workspace()).get(workspace_id, identity)
        assert value.result_id
        from flowfield.results import Results

        result = Results(workspace()).get(workspace_id, value.result_id)
        return await runtimes.prepare(
            workspace_id, InspectionPrepare(result_id=result.id, expected_revision=result.revision)
        )

    return router
