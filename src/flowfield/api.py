"""Local HTTP interface and packaged admin panel."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sse_starlette import EventSourceResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from flowfield import __version__
from flowfield.activity import (
    ActivityCreate,
    ActivityEntry,
    ActivityPage,
    EntryKind,
)
from flowfield.adapters.directory_picker import DirectorySelection, select_directory
from flowfield.application import (
    Board,
    Health,
    Milestone,
    MilestoneCreate,
    MilestoneEdit,
    Project,
    ProjectEdit,
    ProjectSetup,
    ProjectSetupDefaults,
    Task,
    TaskCreate,
    TaskEdit,
    TaskPriority,
    TaskProgress,
    TaskPublish,
    TaskReconcile,
    Workspace,
    health,
)
from flowfield.attachments import attachment_router
from flowfield.attention import attention_counts
from flowfield.browser import browser_router
from flowfield.changes import Changes
from flowfield.context_api import context_router
from flowfield.conversation_api import conversation_router
from flowfield.coordinator_api import coordinator_router
from flowfield.errors import ApplicationError
from flowfield.execution_api import execution_router
from flowfield.guidance import Guidance, GuidanceChange, GuidanceTemplates, GuidanceView, template
from flowfield.harness_api import harness_router
from flowfield.inspection_api import inspection_router
from flowfield.integration_api import integration_router
from flowfield.mcp import create_mcp
from flowfield.notification_api import NotificationService, notification_router
from flowfield.question_api import question_router
from flowfield.result_api import result_router
from flowfield.state import data_path
from flowfield.supervisor import Supervisor


def create_app(*, web_dir: Path | None = None, data_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.changes = Changes()
        app.state.workspace = Workspace(
            data_dir if data_dir is not None else data_path(),
            on_change=app.state.changes.publish,
            on_activity=app.state.changes.publish_activity,
        )
        app.state.supervisor = Supervisor(app.state.workspace)
        await app.state.supervisor.start()
        app.state.notifications = NotificationService(app.state.workspace)
        try:
            await app.state.notifications.start()
            async with mcp.session_manager.run():
                yield
        finally:
            try:
                await app.state.notifications.close()
            finally:
                await app.state.supervisor.close()

    app = FastAPI(title="Flowfield", version=__version__, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if (origin is not None and origin != expected) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            return JSONResponse(
                {
                    "error": {
                        "code": "forbidden_origin",
                        "message": "Cross-origin requests are not allowed",
                    }
                },
                status_code=403,
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, error: ApplicationError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": error.code, "message": error.message}}, status_code=error.status
        )

    def workspace() -> Workspace:
        service: Workspace = app.state.workspace
        return service

    def supervisor() -> Supervisor:
        service: Supervisor = app.state.supervisor
        return service

    Service = Annotated[Workspace, Depends(workspace)]
    router = APIRouter(prefix="/api/projects")

    @router.get("")
    def projects(service: Service) -> list[Project]:
        return service.projects()

    @router.get("/attention-counts")
    def project_attention_counts(service: Service) -> dict[str, int]:
        with service.connection() as db:
            return {
                row[0]: attention_counts(db, row[0]).get("action", 0)
                for row in db.execute("SELECT id FROM projects ORDER BY id").fetchall()
            }

    @app.get("/api/guidance-template")
    def guidance_template() -> GuidanceTemplates:
        return GuidanceTemplates(
            section=template("agents-section.md"), skill=template("flowfield-coordinator/SKILL.md")
        )

    @router.post("/initialize")
    def initialize(request: ProjectSetup, service: Service) -> Project:
        return service.setup_project(request)

    @router.post("/setup-defaults")
    def setup_defaults(request: ProjectSetup, service: Service) -> ProjectSetupDefaults:
        return service.project_setup_defaults(request)

    @router.post("/select-directory")
    def choose_directory() -> DirectorySelection:
        return select_directory()

    @router.get("/{project_id}/guidance")
    def guidance(project_id: str, service: Service) -> GuidanceView:
        return Guidance(service).get(project_id)

    @router.post("/{project_id}/guidance")
    def change_guidance(project_id: str, request: GuidanceChange, service: Service) -> GuidanceView:
        return Guidance(service).change(project_id, request)

    @router.get("/{project_id}")
    def project(project_id: str, service: Service) -> Project:
        return service.project(project_id)

    @router.put("/{project_id}")
    def edit_project(project_id: str, request: ProjectEdit, service: Service) -> Project:
        return service.edit_project(project_id, request)

    @router.get("/{project_id}/activity")
    def activity(
        project_id: str,
        service: Service,
        task_id: str | None = None,
        kind: EntryKind | None = None,
        current_only: bool = False,
        before: int | None = None,
        limit: int = 50,
    ) -> ActivityPage:
        return service.activity(
            project_id,
            task_id=task_id,
            kind=kind,
            current_only=current_only,
            before=before,
            limit=limit,
        )

    @router.post("/{project_id}/activity", status_code=201)
    def add_activity(project_id: str, request: ActivityCreate, service: Service) -> ActivityEntry:
        return service.add_activity(project_id, request)

    @router.get("/{project_id}/activity/{entry_id}")
    def activity_entry(project_id: str, entry_id: str, service: Service) -> ActivityEntry:
        return service.activity_entry(project_id, entry_id)

    @router.get("/{project_id}/board")
    def board(project_id: str, service: Service) -> Board:
        return service.board(project_id)

    @router.get("/{project_id}/milestones")
    def milestones(project_id: str, service: Service) -> list[Milestone]:
        return service.milestones(project_id)

    @router.post("/{project_id}/milestones", status_code=201)
    def create_milestone(project_id: str, request: MilestoneCreate, service: Service) -> Milestone:
        return service.create_milestone(project_id, request)

    @router.get("/{project_id}/milestones/{milestone_id}")
    def milestone(project_id: str, milestone_id: str, service: Service) -> Milestone:
        return service.milestone(project_id, milestone_id)

    @router.put("/{project_id}/milestones/{milestone_id}")
    def edit_milestone(
        project_id: str, milestone_id: str, request: MilestoneEdit, service: Service
    ) -> Milestone:
        return service.edit_milestone(project_id, milestone_id, request)

    @router.get("/{project_id}/tasks")
    def tasks(project_id: str, service: Service, include_archived: bool = False) -> list[Task]:
        return service.tasks(project_id, include_archived=include_archived)

    @router.post("/{project_id}/tasks", status_code=201)
    def create_task(project_id: str, request: TaskCreate, service: Service) -> Task:
        return service.create_task(project_id, request)

    @router.get("/{project_id}/tasks/{task_id}")
    def task(project_id: str, task_id: str, service: Service) -> Task:
        return service.task(project_id, task_id)

    @router.put("/{project_id}/tasks/{task_id}")
    def edit_task(project_id: str, task_id: str, request: TaskEdit, service: Service) -> Task:
        return service.edit_task(project_id, task_id, request)

    @router.post("/{project_id}/tasks/{task_id}/prioritize")
    def prioritize_task(
        project_id: str, task_id: str, request: TaskPriority, service: Service
    ) -> Task:
        return service.prioritize_task(project_id, task_id, request)

    @router.post("/{project_id}/tasks/{task_id}/publish")
    def publish_task(project_id: str, task_id: str, request: TaskPublish, service: Service) -> Task:
        return service.publish_task(project_id, task_id, request)

    @router.post("/{project_id}/tasks/{task_id}/progress")
    def record_progress(
        project_id: str, task_id: str, request: TaskProgress, service: Service
    ) -> Task:
        return service.record_progress(project_id, task_id, request)

    @router.post("/{project_id}/tasks/{task_id}/reconcile")
    def reconcile_task(
        project_id: str, task_id: str, request: TaskReconcile, service: Service
    ) -> Task:
        return service.reconcile_task(project_id, task_id, request)

    app.get("/api/health", response_model=Health)(health)
    app.include_router(router)
    app.include_router(browser_router(workspace))
    app.include_router(context_router(workspace))
    app.include_router(conversation_router(workspace))
    app.include_router(question_router(workspace))
    from flowfield.agent_api import agent_router

    app.include_router(execution_router(supervisor))
    app.include_router(agent_router(supervisor))
    app.include_router(coordinator_router(supervisor))
    app.include_router(harness_router(workspace, supervisor))
    app.include_router(attachment_router(workspace))
    app.include_router(integration_router(supervisor))
    app.include_router(result_router(supervisor))
    app.include_router(inspection_router(lambda: supervisor().workspace))
    app.include_router(notification_router(lambda: app.state.notifications))

    @app.get("/api/events")
    async def events(request: Request) -> EventSourceResponse:
        changes: Changes = request.app.state.changes
        return EventSourceResponse(changes.events(), ping=15, send_timeout=10)

    mcp = create_mcp(workspace, supervisor)
    app.mount("/mcp", mcp.streamable_http_app())

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def missing_api(path: str) -> JSONResponse:
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    assets = web_dir if web_dir is not None else Path(__file__).parent / "_web"
    if (assets / "index.html").is_file():
        # Only UI route prefixes receive the SPA shell; missing assets/API routes stay 404.
        @app.get("/new-project", include_in_schema=False)
        @app.get("/projects/{path:path}", include_in_schema=False)
        def workspace_page() -> FileResponse:
            return FileResponse(assets / "index.html")

        app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    else:

        @app.get("/", include_in_schema=False)
        def missing_web() -> JSONResponse:
            return JSONResponse(
                {"detail": "Admin panel not built. Run: pnpm --dir web build"}, status_code=503
            )

    return app
