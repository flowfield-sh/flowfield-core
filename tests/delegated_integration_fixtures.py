"""Reusable isolated setup and scripted peers; no collected test cases."""

import os

from milestone_delivery_fixtures import setup
from project_fixtures import task_request
from task_reviews_fixtures import verdict

from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import TaskPublish
from flowfield.delivery_models import TaskIntegrationPolicyEdit
from flowfield.execution_models import WorkerResult
from flowfield.milestone_delivery import MilestoneDeliveries
from flowfield.results import Results
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.task_reviews import TaskReviews


def prepared(tmp_path, *, validate=True, prior_report=False):
    execution, integrations, repo, base, milestone, delivery = setup(tmp_path)
    store = execution.workspace
    if prior_report:
        report = store.create_task(
            "harbor",
            task_request(
                title="Earlier report", body="Report repository findings", status="up_next"
            ),
        )
        store.publish_task(
            "harbor",
            report.id,
            TaskPublish(expected_revision=report.revision, completion="report"),
        )
        attempt = execution.claim("harbor", base, {base: set()}, task_id=report.id)
        assert attempt
        execution.started("harbor", attempt.id)
        execution.finish(
            "harbor",
            attempt.id,
            "in_review",
            commit=base,
            result=WorkerResult(summary="Earlier findings", checks="Read the repository"),
        )
        Results(store).process("harbor")
        assert execution.get("harbor", attempt.id).status == "accepted"
    task = store.create_task(
        "harbor",
        task_request(
            title="Contribution", body="Add a file", milestone_id=milestone.id, status="up_next"
        ),
    )
    store.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            expected_revision=task.revision,
            completion="code",
        ),
    )
    run = execution.claim("harbor", base, {base: set()}, task_id=task.id)
    execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(store.directory, repo, run.id, base)
    (env.checkout / "feature.txt").write_text("Combined feature\n")
    candidate, _ = env.snapshot(base)
    execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary="Feature", checks="true"),
        commit=candidate,
    )
    results = Results(store)
    if validate:
        results.process("harbor")
    result = results.page("harbor", task.id).items[0]
    Reviewers(store).configure(
        "harbor",
        "task",
        ReviewerEdit(
            expected_revision=1, enabled=True, selection=AgentChoice(model="review-fixture")
        ),
    )
    return store, repo, base, milestone, delivery, results, result


def policy(store, milestone, delivery, enabled):
    return MilestoneDeliveries(store).integration_policy(
        "harbor",
        milestone.id,
        TaskIntegrationPolicyEdit(
            expected_revision=delivery.integration_policy.revision,
            delivery_id=delivery.id,
            branch=delivery.branch,
            enabled=enabled,
        ),
    )


def assess(store, result):
    reviews = TaskReviews(store)
    review = reviews.prepare("harbor", result.id)
    review = reviews.claim("harbor", review.id, review.revision)
    review = reviews.started("harbor", review.id, review.revision)
    return reviews.conclude("harbor", review.id, review.revision, verdict(), cleanup_confirmed=True)


def workspace_policy(store, enabled, revision=1):
    from flowfield.delivery_models import WorkspaceIntegrationPolicyEdit

    return MilestoneDeliveries(store).configure_workspace_policy(
        "harbor", WorkspaceIntegrationPolicyEdit(expected_revision=revision, enabled=enabled)
    )
