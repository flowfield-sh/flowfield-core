"""Explicit workspace entry and shared settings; repository adoption creates no defaults."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Query

from flowfield.agent_models import AgentSettingsEdit, AgentSettingsView
from flowfield.application import (
    Edit,
    Milestone,
    MilestoneCreate,
    MilestoneEdit,
    Project,
    ProjectSetup,
)
from flowfield.attention import (
    AttentionColumn,
    AttentionPage,
    attention_page,
    workspace_attention_counts,
)
from flowfield.browser import BrowserReads, BrowserWorkspaceBoard
from flowfield.documents import (
    Document,
    DocumentCreate,
    DocumentEdit,
    DocumentPage,
    DocumentRestore,
    Documents,
)
from flowfield.execution_models import (
    ModelOption,
    QueueEdit,
    SettingsEdit,
    WorkerOccupancy,
    WorkspaceWorkerSettings,
)
from flowfield.harness_models import HarnessKind
from flowfield.labels import LabelCreate, LabelEdit, Labels, WorkspaceLabel
from flowfield.milestones import Milestones
from flowfield.permission_models import PermissionAnswer, PermissionPage, PermissionRecord
from flowfield.supervisor import Supervisor
from flowfield.workspaces import (
    ProjectAdoption,
    WorkspaceCreate,
    WorkspaceEdit,
    WorkspaceRecord,
    Workspaces,
)


def workspace_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces")

    def spaces() -> Workspaces:
        return Workspaces(supervisor().workspace)

    @router.get("")
    def all_workspaces() -> list[WorkspaceRecord]:
        return spaces().all()

    @router.post("", status_code=201)
    def create(request: WorkspaceCreate) -> WorkspaceRecord:
        return spaces().create(request)

    @router.get("/attention-counts")
    def counts() -> dict[str, int]:
        with supervisor().workspace.connection() as db:
            return {
                row[0]: workspace_attention_counts(db, row[0]).get("action", 0)
                for row in db.execute("SELECT id FROM workspaces")
            }

    @router.get("/{workspace_id}/view/board")
    def board(workspace_id: str) -> BrowserWorkspaceBoard:
        return BrowserReads(supervisor().workspace).workspace_board(workspace_id)

    @router.get("/{workspace_id}/view/attention")
    def attention(
        workspace_id: str,
        column: AttentionColumn = "action",
        project_id: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> AttentionPage:
        return attention_page(
            supervisor().workspace, project_id, column, offset, limit, workspace_id=workspace_id
        )

    @router.get("/{workspace_id}")
    def get(workspace_id: str) -> WorkspaceRecord:
        return spaces().get(workspace_id)

    @router.put("/{workspace_id}")
    def edit(workspace_id: str, request: WorkspaceEdit) -> WorkspaceRecord:
        return spaces().edit(workspace_id, request)

    @router.get("/{workspace_id}/projects")
    def projects(workspace_id: str) -> list[Project]:
        return spaces().projects(workspace_id)

    @router.post("/{workspace_id}/projects", status_code=201)
    def adopt(workspace_id: str, request: ProjectAdoption) -> Project:
        return spaces().adopt(workspace_id, ProjectSetup(**request.model_dump()))

    @router.get("/{workspace_id}/models")
    async def models(
        workspace_id: str, harness: HarnessKind = "codex", refresh: bool = False
    ) -> list[ModelOption]:
        return await supervisor().workspace_models(workspace_id, harness=harness, refresh=refresh)

    @router.get("/{workspace_id}/coordinator-settings")
    def coordinator(workspace_id: str) -> AgentSettingsView:
        return spaces().coordinator(workspace_id)

    @router.put("/{workspace_id}/coordinator-settings")
    async def configure_coordinator(
        workspace_id: str, request: AgentSettingsEdit
    ) -> AgentSettingsView:
        return await supervisor().configure_workspace_coordinator(workspace_id, request)

    @router.get("/{workspace_id}/workers")
    def workers(workspace_id: str) -> WorkspaceWorkerSettings:
        return spaces().workers(workspace_id)

    @router.put("/{workspace_id}/workers")
    async def configure_workers(
        workspace_id: str, request: SettingsEdit
    ) -> WorkspaceWorkerSettings:
        return await supervisor().configure_workspace_workers(workspace_id, request)

    @router.get("/{workspace_id}/workers/occupancy")
    def occupancy(workspace_id: str) -> WorkerOccupancy:
        return spaces().occupancy(workspace_id)

    @router.post("/{workspace_id}/queue")
    def queue(workspace_id: str, request: QueueEdit) -> WorkspaceWorkerSettings:
        return spaces().queue(workspace_id, request)

    @router.get("/{workspace_id}/permissions")
    def permissions(
        workspace_id: str,
        before: int | None = Query(None, ge=1),
        limit: int = Query(50, ge=1, le=100),
    ) -> PermissionPage:
        return supervisor().permissions.page(
            workspace_id, before=before, limit=limit, workspace_scope=True
        )

    @router.get("/{workspace_id}/permissions/{permission_id}")
    def permission(workspace_id: str, permission_id: str) -> PermissionRecord:
        service = supervisor()
        with service.workspace.connection() as db:
            return service.permissions._get(db, workspace_id, permission_id, workspace_scope=True)

    @router.post("/{workspace_id}/permissions/{permission_id}/answer")
    async def answer(
        workspace_id: str, permission_id: str, request: PermissionAnswer
    ) -> PermissionRecord:
        return supervisor().permissions.answer(
            workspace_id, permission_id, request, workspace_scope=True
        )

    @router.get("/{workspace_id}/milestones")
    def milestones(workspace_id: str) -> list[Milestone]:
        return Milestones(supervisor().workspace).all(workspace_id)

    @router.post("/{workspace_id}/milestones", status_code=201)
    def create_milestone(workspace_id: str, request: MilestoneCreate) -> Milestone:
        return Milestones(supervisor().workspace).create(workspace_id, request)

    @router.get("/{workspace_id}/milestones/{identity}")
    def milestone(workspace_id: str, identity: str) -> Milestone:
        return Milestones(supervisor().workspace).get(workspace_id, identity)

    @router.put("/{workspace_id}/milestones/{identity}")
    def edit_milestone(workspace_id: str, identity: str, request: MilestoneEdit) -> Milestone:
        return Milestones(supervisor().workspace).edit(workspace_id, identity, request)

    @router.get("/{workspace_id}/milestones/{identity}/text")
    def milestone_text(
        workspace_id: str,
        identity: str,
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        return Milestones(supervisor().workspace).text(
            workspace_id, identity, revision=revision, offset=offset, limit=limit
        )

    @router.get("/{workspace_id}/labels")
    def labels(workspace_id: str) -> list[WorkspaceLabel]:
        return Labels(supervisor().workspace).all(workspace_id)

    @router.post("/{workspace_id}/labels", status_code=201)
    def create_label(workspace_id: str, request: LabelCreate) -> WorkspaceLabel:
        return Labels(supervisor().workspace).create(workspace_id, request)

    @router.put("/{workspace_id}/labels/{identity}")
    def edit_label(workspace_id: str, identity: str, request: LabelEdit) -> WorkspaceLabel:
        return Labels(supervisor().workspace).edit(workspace_id, identity, request)

    @router.delete("/{workspace_id}/labels/{identity}")
    def delete_label(workspace_id: str, identity: str, request: Edit) -> WorkspaceLabel:
        return Labels(supervisor().workspace).delete(workspace_id, identity, request)

    @router.get("/{workspace_id}/documents")
    def documents(
        workspace_id: str, after: str | None = None, limit: int = Query(20, ge=1, le=50)
    ) -> DocumentPage:
        return Documents(supervisor().workspace).page(workspace_id, after=after, limit=limit)

    @router.post("/{workspace_id}/documents", status_code=201)
    def create_document(workspace_id: str, request: DocumentCreate) -> Document:
        return Documents(supervisor().workspace).create(workspace_id, request)

    @router.get("/{workspace_id}/documents/{identity}")
    def document(
        workspace_id: str, identity: str, revision: int | None = Query(None, ge=1)
    ) -> Document:
        return Documents(supervisor().workspace).get(workspace_id, identity, revision)

    @router.put("/{workspace_id}/documents/{identity}")
    def edit_document(workspace_id: str, identity: str, request: DocumentEdit) -> Document:
        return Documents(supervisor().workspace).edit(workspace_id, identity, request)

    @router.delete("/{workspace_id}/documents/{identity}")
    def delete_document(workspace_id: str, identity: str, request: Edit) -> Document:
        return Documents(supervisor().workspace).delete(workspace_id, identity, request)

    @router.get("/{workspace_id}/documents/{identity}/history")
    def document_history(
        workspace_id: str,
        identity: str,
        before: int | None = Query(None, ge=1),
        limit: int = Query(20, ge=1, le=50),
    ) -> DocumentPage:
        return Documents(supervisor().workspace).history(
            workspace_id, identity, before=before, limit=limit
        )

    @router.post("/{workspace_id}/documents/{identity}/restore")
    def restore_document(workspace_id: str, identity: str, request: DocumentRestore) -> Document:
        return Documents(supervisor().workspace).restore(workspace_id, identity, request)

    return router
