"""Focused HTTP context views for command-line and other agent clients."""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request

from flowfield.activity import EntryKind
from flowfield.application import Workspace
from flowfield.milestones import Milestones
from flowfield.reads import ContextReads, excerpt
from flowfield.search import Entity, History, Search


def context_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/context/projects")

    def reads(request: Request) -> ContextReads:
        return ContextReads(workspace(), f"{request.url.scheme}://{request.url.netloc}")

    Reads = Annotated[ContextReads, Depends(reads)]

    @router.get("")
    def projects(context: Reads, after: str | None = None, limit: int = 20) -> dict[str, Any]:
        return context.projects(after=after, limit=limit)

    @router.get("/{project_id}")
    def project(context: Reads, project_id: str) -> dict[str, Any]:
        return context.project(project_id)

    @router.get("/{project_id}/board")
    def overview(context: Reads, project_id: str) -> dict[str, Any]:
        return context.overview(project_id)

    @router.get("/{project_id}/search")
    def search(
        context: Reads,
        project_id: str,
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
            project_id,
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

    @router.get("/{project_id}/tasks")
    def tasks(
        context: Reads,
        project_id: str,
        include_archived: bool = False,
        status: str | None = None,
        label_id: str | None = None,
        milestone_id: str | None = None,
        readiness: str | None = None,
        query: str | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return context.tasks(
            project_id,
            include_archived=include_archived,
            status=status,
            label_id=label_id,
            milestone_id=milestone_id,
            readiness=readiness,
            query=query,
            after=after,
            limit=limit,
        )

    @router.get("/{project_id}/tasks/{task_id}")
    def task(context: Reads, project_id: str, task_id: str) -> dict[str, Any]:
        return context.task(project_id, task_id)

    @router.get("/{project_id}/tasks/{task_id}/relationships")
    def relationships(
        context: Reads,
        project_id: str,
        task_id: str,
        relation: Literal["prerequisites", "blocked_by", "dependents"] = "prerequisites",
        after: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return context.relationships(
            project_id, task_id, relation=relation, after=after, limit=limit
        )

    @router.get("/{project_id}/tasks/{task_id}/revisions")
    def revisions(
        context: Reads, project_id: str, task_id: str, before: int | None = None, limit: int = 20
    ) -> dict[str, Any]:
        return context.revisions(project_id, task_id, before=before, limit=limit)

    @router.get("/{project_id}/activity")
    def activity(
        context: Reads,
        project_id: str,
        task_id: str | None = None,
        kind: EntryKind | None = None,
        current_only: bool = False,
        before: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return context.activity(
            project_id,
            task_id=task_id,
            kind=kind,
            current_only=current_only,
            before=before,
            limit=limit,
        )

    @router.get("/{project_id}/activity/{entry_id}")
    def activity_entry(context: Reads, project_id: str, entry_id: str) -> dict[str, Any]:
        return context.activity_entry(project_id, entry_id)

    @router.get("/{project_id}/text")
    def text(
        context: Reads,
        project_id: str,
        resource: Literal["task", "project", "activity", "question", "result"],
        identity: str | None = None,
        field: str = "body",
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        return context.text(project_id, resource, identity, field, revision, offset, limit)

    @router.get("/{project_id}/questions")
    def questions(
        context: Reads,
        project_id: str,
        status: str = "active",
        task_id: str | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return context.questions(
            project_id, status=status, task_id=task_id, after=after, limit=limit
        )

    @router.get("/{project_id}/questions/{identity}")
    def question(
        context: Reads, project_id: str, identity: str, revision: int | None = None
    ) -> dict[str, Any]:
        return context.question(project_id, identity, revision)

    return router


def workspace_context_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/context/workspaces/{workspace_id}")

    @router.get("/milestones")
    def milestones(workspace_id: str, after: str | None = None, limit: int = 20) -> dict[str, Any]:
        return Milestones(workspace()).page(workspace_id, after=after, limit=limit)

    @router.get("/milestones/{identity}")
    def milestone(workspace_id: str, identity: str) -> dict[str, Any]:
        return excerpt(Milestones(workspace()).get(workspace_id, identity).model_dump())

    @router.get("/milestones/{identity}/text")
    def milestone_text(
        workspace_id: str,
        identity: str,
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        return Milestones(workspace()).text(
            workspace_id, identity, revision=revision, offset=offset, limit=limit
        )

    @router.get("/coordinator-history")
    def history(
        workspace_id: str, request: Request, before: int | None = None, limit: int = 20
    ) -> dict[str, Any]:
        return ContextReads(workspace(), str(request.base_url).rstrip("/")).coordinator_history(
            workspace_id, before=before, limit=limit
        )

    @router.get("/text")
    def text(
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
