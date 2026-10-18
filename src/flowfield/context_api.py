"""Focused HTTP context views for command-line and other agent clients."""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request

from flowfield.activity import EntryKind
from flowfield.application import Workspace
from flowfield.attention import AttentionColumn, RequestKind
from flowfield.reads import ContextReads
from flowfield.search import Entity, History, Search


def context_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/context")

    def reads(request: Request) -> ContextReads:
        return ContextReads(workspace(), f"{request.url.scheme}://{request.url.netloc}")

    Reads = Annotated[ContextReads, Depends(reads)]

    @router.get("/projects")
    def projects(context: Reads, after: str | None = None, limit: int = 20) -> dict[str, Any]:
        return context.projects(after=after, limit=limit)

    @router.get("/projects/{project_id}")
    def project(context: Reads, project_id: str) -> dict[str, Any]:
        return context.project(project_id)

    @router.get("/workspaces/{workspace_id}/board")
    def overview(context: Reads, workspace_id: str) -> dict[str, Any]:
        return context.overview(workspace_id)

    @router.get("/workspaces/{workspace_id}/search")
    def search(
        context: Reads,
        workspace_id: str,
        query: str,
        entity: Entity | None = None,
        kind: str | None = None,
        task_id: str | None = None,
        history: History = "current",
        since: str | None = None,
        until: str | None = None,
        before: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return Search(context.workspace, context.browser_origin).page(
            workspace_id,
            query,
            entity=entity,
            kind=kind,
            task_id=task_id,
            history=history,
            since=since,
            until=until,
            before=before,
            limit=limit,
        )

    @router.get("/workspaces/{workspace_id}/tasks")
    def tasks(
        context: Reads,
        workspace_id: str,
        include_archived: bool = False,
        status: str | None = None,
        label_id: str | None = None,
        readiness: str | None = None,
        query: str | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return context.tasks(
            workspace_id,
            include_archived=include_archived,
            status=status,
            label_id=label_id,
            readiness=readiness,
            query=query,
            after=after,
            limit=limit,
        )

    @router.get("/workspaces/{workspace_id}/tasks/{task_id}")
    def task(context: Reads, workspace_id: str, task_id: str) -> dict[str, Any]:
        return context.task(workspace_id, task_id)

    @router.get("/workspaces/{workspace_id}/tasks/{task_id}/relationships")
    def relationships(
        context: Reads,
        workspace_id: str,
        task_id: str,
        relation: Literal["prerequisites", "blocked_by", "dependents"] = "prerequisites",
        after: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return context.relationships(
            workspace_id, task_id, relation=relation, after=after, limit=limit
        )

    @router.get("/workspaces/{workspace_id}/tasks/{task_id}/revisions")
    def revisions(
        context: Reads, workspace_id: str, task_id: str, before: int | None = None, limit: int = 20
    ) -> dict[str, Any]:
        return context.revisions(workspace_id, task_id, before=before, limit=limit)

    @router.get("/workspaces/{workspace_id}/activity")
    def activity(
        context: Reads,
        workspace_id: str,
        task_id: str | None = None,
        kind: EntryKind | None = None,
        current_only: bool = False,
        before: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return context.activity(
            workspace_id,
            task_id=task_id,
            kind=kind,
            current_only=current_only,
            before=before,
            limit=limit,
        )

    @router.get("/workspaces/{workspace_id}/activity/{entry_id}")
    def activity_entry(context: Reads, workspace_id: str, entry_id: str) -> dict[str, Any]:
        return context.activity_entry(workspace_id, entry_id)

    @router.get("/workspaces/{workspace_id}/text")
    def text(
        context: Reads,
        workspace_id: str,
        resource: Literal["task", "project", "activity", "question", "result"],
        identity: str | None = None,
        field: str = "body",
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        return context.text(workspace_id, resource, identity, field, revision, offset, limit)

    @router.get("/workspaces/{workspace_id}/questions")
    def questions(
        context: Reads,
        workspace_id: str,
        status: str = "active",
        task_id: str | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return context.questions(
            workspace_id, status=status, task_id=task_id, after=after, limit=limit
        )

    @router.get("/workspaces/{workspace_id}/questions/{identity}")
    def question(
        context: Reads, workspace_id: str, identity: str, revision: int | None = None
    ) -> dict[str, Any]:
        return context.question(workspace_id, identity, revision)

    return router


def workspace_context_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/context/workspaces/{workspace_id}")

    @router.get("/requests")
    def requests(
        workspace_id: str,
        request: Request,
        column: AttentionColumn = "action",
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        return ContextReads(workspace(), str(request.base_url).rstrip("/")).requests(
            workspace_id, column, offset, limit
        )

    @router.get("/requests/{kind}/{identity}")
    def request_detail(
        workspace_id: str,
        kind: RequestKind,
        identity: str,
        request: Request,
    ) -> dict[str, Any]:
        return ContextReads(workspace(), str(request.base_url).rstrip("/")).request(
            workspace_id, kind, identity
        )

    @router.get("/coordinator-history")
    def history(
        workspace_id: str, request: Request, before: int | None = None, limit: int = 20
    ) -> dict[str, Any]:
        return ContextReads(workspace(), str(request.base_url).rstrip("/")).coordinator_history(
            workspace_id, before=before, limit=limit
        )

    @router.get("/coordinator-text")
    def coordinator_text(
        workspace_id: str,
        identity: str,
        field: Literal["human", "coordinator"],
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        return ContextReads(workspace()).text(
            workspace_id, "coordinator", identity, field, revision, offset, limit
        )

    return router
