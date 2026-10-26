"""Explicit workspace entry and shared settings; repository adoption creates no defaults."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import StringConstraints

from flowfield.agent_models import AgentSettingsEdit, AgentSettingsView
from flowfield.application import (
    Edit,
    Project,
    ProjectSetup,
)
from flowfield.attention import (
    AttentionColumn,
    AttentionItem,
    AttentionPage,
    RequestKind,
    attention_page,
    workspace_attention_counts,
)
from flowfield.browser import BrowserReads, BrowserWorkspaceBoard
from flowfield.deliveries import DeliveredPage, delivery_page
from flowfield.document_settings import (
    DocumentSettings,
    DocumentSettingsEdit,
    DocumentSettingsService,
)
from flowfield.document_usage import DocumentUsage, DocumentUsageEdit, DocumentUsageView
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
from flowfield.subtasks import Subtask, SubtaskCreate, SubtaskEdit, Subtasks
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
        project_id: str | None = None,
        column: AttentionColumn = "action",
        include_answered: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> AttentionPage:
        return attention_page(
            supervisor().workspace,
            workspace_id,
            column,
            offset,
            limit,
            include_answered=include_answered,
            project_id=project_id,
        )

    @router.get("/{workspace_id}/view/resource-presence")
    def resource_presence(
        workspace_id: str,
        location: Annotated[
            list[Annotated[str, StringConstraints(max_length=4096)]],
            Query(max_length=24),
        ],
    ) -> dict[str, bool]:
        from flowfield.browser_tabs import resource_presence

        return resource_presence(supervisor().workspace, workspace_id, location)

    @router.get("/{workspace_id}/view/delivered")
    def delivered(
        workspace_id: str,
        project_id: str | None = None,
        before: str | None = None,
        limit: int = Query(20, ge=1, le=50),
    ) -> DeliveredPage:
        return delivery_page(supervisor().workspace, workspace_id, project_id, before, limit)

    @router.get("/{workspace_id}/requests/{kind}/{identity}")
    def request_detail(workspace_id: str, kind: RequestKind, identity: str) -> AttentionItem:
        from flowfield.requests import get_request

        return get_request(supervisor().workspace, workspace_id, kind, identity)

    @router.get("/{workspace_id}")
    def get(workspace_id: str) -> WorkspaceRecord:
        return spaces().get(workspace_id)

    @router.put("/{workspace_id}")
    def edit(workspace_id: str, request: WorkspaceEdit) -> WorkspaceRecord:
        return spaces().edit(workspace_id, request)

    @router.get("/{workspace_id}/projects")
    def projects(workspace_id: str) -> list[Project]:
        return spaces().projects(workspace_id)

    @router.get("/{workspace_id}/tasks/{task_id}/subtasks")
    def subtasks(workspace_id: str, task_id: str) -> list[Subtask]:
        return Subtasks(supervisor().workspace).all(workspace_id, task_id)

    @router.post("/{workspace_id}/tasks/{task_id}/subtasks", status_code=201)
    def create_subtask(workspace_id: str, task_id: str, request: SubtaskCreate) -> Subtask:
        return Subtasks(supervisor().workspace).create(workspace_id, task_id, request)

    @router.get("/{workspace_id}/tasks/{task_id}/subtasks/{identity}")
    def subtask(workspace_id: str, task_id: str, identity: str) -> Subtask:
        return Subtasks(supervisor().workspace).get(workspace_id, task_id, identity)

    @router.put("/{workspace_id}/tasks/{task_id}/subtasks/{identity}")
    def edit_subtask(
        workspace_id: str, task_id: str, identity: str, request: SubtaskEdit
    ) -> Subtask:
        return Subtasks(supervisor().workspace).edit(workspace_id, task_id, identity, request)

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

    @router.get("/{workspace_id}/documents/{identity}/usage")
    def document_usage(workspace_id: str, identity: str) -> DocumentUsageView:
        return DocumentUsage(supervisor().workspace).get(workspace_id, identity)

    @router.get("/{workspace_id}/documents/{identity}/settings")
    def document_settings(workspace_id: str, identity: str) -> DocumentSettings:
        return DocumentSettingsService(supervisor().workspace).get(workspace_id, identity)

    @router.put("/{workspace_id}/documents/{identity}/settings")
    def edit_document_settings(
        workspace_id: str, identity: str, request: DocumentSettingsEdit
    ) -> DocumentSettings:
        return DocumentSettingsService(supervisor().workspace).edit(workspace_id, identity, request)

    @router.put("/{workspace_id}/documents/{identity}/usage")
    def edit_document_usage(
        workspace_id: str, identity: str, request: DocumentUsageEdit
    ) -> DocumentUsageView:
        return DocumentUsage(supervisor().workspace).edit(workspace_id, identity, request)

    @router.post("/{workspace_id}/documents/{identity}/restore")
    def restore_document(workspace_id: str, identity: str, request: DocumentRestore) -> Document:
        return Documents(supervisor().workspace).restore(workspace_id, identity, request)

    return router
