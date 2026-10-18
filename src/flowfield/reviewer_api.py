"""Human reviewer configuration and exact scoped review recovery."""

from collections.abc import Callable

from fastapi import APIRouter, Query

from flowfield.execution_models import RunAction
from flowfield.reviewer_settings import ReviewerEdit, Reviewers, ReviewerSettingsView
from flowfield.run_activity import RunActivityPage
from flowfield.supervisor import Supervisor
from flowfield.task_reviews import ReviewAttemptInfo, ReviewAttemptPage, TaskReview


def reviewer_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/workspaces/{workspace}/reviewer")
    def settings(workspace: str, entity: str = "") -> ReviewerSettingsView:
        return Reviewers(supervisor().workspace).get(workspace, entity=entity)

    @router.put("/workspaces/{workspace}/reviewer")
    async def configure(
        workspace: str,
        request: ReviewerEdit,
        entity: str = "",
    ) -> ReviewerSettingsView:
        service = supervisor()
        owner = Reviewers(service.workspace)
        current = owner.get(workspace, entity=entity)
        service.workspace._current(current.revision, request.expected_revision)
        if request.selection and request.enabled is not False:
            from flowfield.adapters.coordinator_directory import prepare

            await service.validate_agent_choice(
                request.selection,
                None,
                project_path=str(prepare(service.workspace, workspace)),
            )
        return owner.configure(workspace, request, entity=entity)

    @router.get("/workspaces/{workspace}/tasks/{task}/review")
    def current(workspace: str, task: str) -> TaskReview | None:
        return supervisor().review_execution.reviews.latest(workspace, task)

    @router.get("/workspaces/{workspace}/reviews/{identity}")
    def review(workspace: str, identity: str) -> TaskReview:
        return supervisor().review_execution.reviews.get(workspace, identity)

    @router.get("/workspaces/{workspace}/reviews/{identity}/attempts")
    def attempts(
        workspace: str, identity: str, before: int | None = Query(default=None, ge=1)
    ) -> ReviewAttemptPage:
        page = supervisor().review_execution.reviews.attempts(workspace, identity, before)
        return ReviewAttemptPage(
            items=[
                ReviewAttemptInfo(
                    **{
                        key: value
                        for key, value in item.items()
                        if key in ReviewAttemptInfo.model_fields
                    }
                )
                for item in page["items"]
            ],
            next_before=page["next_before"],
        )

    @router.post("/workspaces/{workspace}/reviews/{identity}/stop")
    async def stop(workspace: str, identity: str, request: RunAction) -> TaskReview:
        return await supervisor().review_execution.stop(
            workspace, identity, request.expected_revision
        )

    @router.get("/workspaces/{workspace}/reviews/{identity}/activity")
    def activity(workspace: str, identity: str) -> RunActivityPage:
        return supervisor().review_execution.reviews.activity(workspace, identity)

    @router.get("/workspaces/{workspace}/reviews/{identity}/attempts/{attempt}/activity")
    def attempt_activity(workspace: str, identity: str, attempt: str) -> RunActivityPage:
        return supervisor().review_execution.reviews.activity(workspace, identity, attempt)

    @router.post("/workspaces/{workspace}/reviews/{identity}/retry")
    def retry(workspace: str, identity: str, request: RunAction) -> TaskReview:
        return supervisor().review_execution.reviews.retry(
            workspace, identity, request.expected_revision
        )

    @router.post("/workspaces/{workspace}/reviews/{identity}/confirm-stopped")
    def confirm_stopped(workspace: str, identity: str, request: RunAction) -> TaskReview:
        return supervisor().review_execution.confirm_stopped(
            workspace, identity, request.expected_revision
        )

    return router
