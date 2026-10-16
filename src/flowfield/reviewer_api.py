"""Human reviewer configuration and exact scoped review recovery."""

from collections.abc import Callable

from fastapi import APIRouter, Query

from flowfield.execution_models import RunAction
from flowfield.milestone_reviews import MilestoneReview
from flowfield.reviewer_settings import ReviewerEdit, Reviewers, ReviewerSettingsView, ReviewPurpose
from flowfield.run_activity import RunActivityPage
from flowfield.supervisor import Supervisor
from flowfield.task_reviews import ReviewAttemptInfo, ReviewAttemptPage, TaskReview


def reviewer_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/workspaces/{workspace}/reviewers/{purpose}")
    def settings(
        workspace: str, purpose: ReviewPurpose, project: str = "", entity: str = ""
    ) -> ReviewerSettingsView:
        return Reviewers(supervisor().workspace).get(
            workspace, purpose, project=project, entity=entity
        )

    @router.put("/workspaces/{workspace}/reviewers/{purpose}")
    async def configure(
        workspace: str,
        purpose: ReviewPurpose,
        request: ReviewerEdit,
        project: str = "",
        entity: str = "",
    ) -> ReviewerSettingsView:
        service = supervisor()
        owner = Reviewers(service.workspace)
        current = owner.get(workspace, purpose, project=project, entity=entity)
        service.workspace._current(current.revision, request.expected_revision)
        if request.selection:
            from flowfield.adapters.coordinator_directory import prepare

            await service.validate_agent_choice(
                request.selection,
                project or None,
                project_path=None if project else str(prepare(service.workspace, workspace)),
            )
        return owner.configure(workspace, purpose, request, project=project, entity=entity)

    @router.get("/projects/{project}/tasks/{task}/review")
    def current(project: str, task: str) -> TaskReview | None:
        return supervisor().review_execution.reviews.latest(project, task)

    @router.get("/projects/{project}/reviews/{identity}")
    def review(project: str, identity: str) -> TaskReview:
        return supervisor().review_execution.reviews.get(project, identity)

    @router.get("/projects/{project}/milestone-reviews/{identity}")
    def milestone_review(project: str, identity: str) -> MilestoneReview:
        return supervisor().milestone_review_execution.reviews.get(project, identity)

    @router.get("/projects/{project}/milestone-reviews/{identity}/attempts")
    def milestone_attempts(
        project: str, identity: str, before: int | None = Query(default=None, ge=1)
    ) -> ReviewAttemptPage:
        page = supervisor().milestone_review_execution.reviews.attempts(project, identity, before)
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

    @router.get("/projects/{project}/milestone-reviews/{identity}/activity")
    def milestone_activity(project: str, identity: str) -> RunActivityPage:
        return supervisor().milestone_review_execution.reviews.activity(project, identity)

    @router.get("/projects/{project}/milestone-reviews/{identity}/attempts/{attempt}/activity")
    def milestone_attempt_activity(project: str, identity: str, attempt: str) -> RunActivityPage:
        return supervisor().milestone_review_execution.reviews.activity(project, identity, attempt)

    @router.post("/projects/{project}/milestone-reviews/{identity}/stop")
    async def stop_milestone_review(
        project: str, identity: str, request: RunAction
    ) -> MilestoneReview:
        return await supervisor().milestone_review_execution.stop(
            project, identity, request.expected_revision
        )

    @router.post("/projects/{project}/milestone-reviews/{identity}/retry")
    def retry_milestone_review(project: str, identity: str, request: RunAction) -> MilestoneReview:
        return supervisor().milestone_review_execution.reviews.retry(
            project, identity, request.expected_revision
        )

    @router.post("/projects/{project}/milestone-reviews/{identity}/confirm-stopped")
    def confirm_milestone_review_stopped(
        project: str, identity: str, request: RunAction
    ) -> MilestoneReview:
        return supervisor().milestone_review_execution.confirm_stopped(
            project, identity, request.expected_revision
        )

    @router.get("/projects/{project}/reviews/{identity}/attempts")
    def attempts(
        project: str, identity: str, before: int | None = Query(default=None, ge=1)
    ) -> ReviewAttemptPage:
        page = supervisor().review_execution.reviews.attempts(project, identity, before)
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

    @router.post("/projects/{project}/reviews/{identity}/stop")
    async def stop(project: str, identity: str, request: RunAction) -> TaskReview:
        return await supervisor().review_execution.stop(
            project, identity, request.expected_revision
        )

    @router.get("/projects/{project}/reviews/{identity}/activity")
    def activity(project: str, identity: str) -> RunActivityPage:
        return supervisor().review_execution.reviews.activity(project, identity)

    @router.get("/projects/{project}/reviews/{identity}/attempts/{attempt}/activity")
    def attempt_activity(project: str, identity: str, attempt: str) -> RunActivityPage:
        return supervisor().review_execution.reviews.activity(project, identity, attempt)

    @router.post("/projects/{project}/reviews/{identity}/retry")
    def retry(project: str, identity: str, request: RunAction) -> TaskReview:
        return supervisor().review_execution.reviews.retry(
            project, identity, request.expected_revision
        )

    @router.post("/projects/{project}/reviews/{identity}/confirm-stopped")
    def confirm_stopped(project: str, identity: str, request: RunAction) -> TaskReview:
        return supervisor().review_execution.confirm_stopped(
            project, identity, request.expected_revision
        )

    return router
