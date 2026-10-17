"""One bounded projection for whole-outcome setup, current ownership and recovery."""

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.milestone_delivery import MilestoneDeliveries
from flowfield.milestone_reviews import MilestoneReview
from flowfield.milestone_verification import delivered_tasks
from flowfield.milestones import Milestones
from flowfield.reviewer_settings import Reviewers, ReviewerSettingsView
from flowfield.verification_plans import VerificationPlan, read
from flowfield.work_scopes import workspace_scope
from flowfield.worker_settings import read_project


class MilestoneReviewStatus(Record):
    plan: VerificationPlan | None = None
    reviewer: ReviewerSettingsView
    review: MilestoneReview | None = None
    message: str


def status(store: Workspace, workspace: str, identity: str) -> MilestoneReviewStatus:
    delivery = MilestoneDeliveries(store).get(workspace, identity)
    scope = delivery.project_id if delivery else workspace_scope(workspace)
    with store.connection() as db:
        milestone = Milestones.read(db, workspace, identity)
        plan = read(db, workspace, milestone.id)
        reviewer = Reviewers(store).resolve(db, workspace, "milestone", entity=milestone.id)
        row = db.execute(
            "SELECT r.data,v.status,json_extract(v.data,'$.current') "
            "FROM milestone_reviews r JOIN milestone_verifications v "
            "ON v.id=r.verification_id WHERE v.workspace_id=? AND v.milestone_id=? "
            "ORDER BY r.number DESC LIMIT 1",
            (workspace, milestone.id),
        ).fetchone()
        review = MilestoneReview.model_validate_json(row[0]) if row else None
        if not reviewer.effective.enabled:
            message = "Automatic milestone assessment is off."
        elif not reviewer.effective.agent:
            message = "Choose a milestone reviewer below to run automatic verification."
        elif not plan:
            message = "Ask the Coordinator to save the whole-outcome verification plan "
            message += "during preparation."
        elif plan.milestone_revision != milestone.revision:
            message = (
                "The milestone changed. Ask the Coordinator to reconcile its verification plan."
            )
        elif (
            row
            and row[2]
            and row[1]
            in ("ready", "accepted", "promoting", "delivery_blocked", "changes_requested")
        ):
            message = {
                "ready": "The combined outcome is ready for your review.",
                "accepted": "The verified milestone has been delivered.",
                "promoting": "Your exact approval is saved; milestone promotion is in progress.",
                "delivery_blocked": "Milestone promotion needs attention in Review.",
                "changes_requested": (
                    "Your feedback is saved. Ask the Coordinator to arrange the follow-up."
                ),
            }[row[1]]
        elif review and review.status in (
            "failed",
            "uncertain",
            "stopped",
            "waiting",
            "running",
            "preparing",
            "stale",
        ):
            message = review.problem or {
                "waiting": "Waiting for your answer",
                "running": "Reviewing the combined outcome",
                "preparing": "Preparing milestone review",
            }.get(review.status, "Milestone review needs attention")
        elif not read_project(db, scope).enabled:
            message = "The queue is paused. Saved verification starts when the queue resumes."
        else:
            try:
                tasks = delivered_tasks(db, workspace, milestone.id, (scope, None))
                message = (
                    "Combined assessment recorded."
                    if review and review.status == "decided"
                    else "Preparing combined verification."
                )
                if not delivery and any(
                    db.execute(
                        "SELECT json_extract(data,'$.completion') FROM result_versions WHERE id=?",
                        (task.result_id,),
                    ).fetchone()[0]
                    != "report"
                    for task in tasks
                ):
                    message = "Configure milestone delivery before reviewing combined code."
            except ApplicationError as error:
                message = str(error)
        return MilestoneReviewStatus(plan=plan, reviewer=reviewer, review=review, message=message)
