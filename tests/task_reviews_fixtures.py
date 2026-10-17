"""Reusable isolated setup and scripted peers; no collected test cases."""

from result_fixtures import fixture

from flowfield.agent_models import AgentChoice
from flowfield.execution_models import QueueEdit
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.task_reviews import ReviewVerdict, TaskReviews


def configured(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    reviewers = Reviewers(service.workspace)
    reviewers.configure(
        "harbor",
        "task",
        ReviewerEdit(
            expected_revision=1,
            enabled=True,
            selection=AgentChoice(model="review-fixture"),
        ),
    )
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    return service, repo, run, reviewers, TaskReviews(service.workspace)


def verdict(action="approve"):
    return ReviewVerdict(
        action=action,
        summary="Checked the agreed outcome",
        evidence="Candidate checks passed; diff matches the requested result.",
        request="" if action == "approve" else "Explain the error-handling case.",
    )
