"""Real Git/evidence owners with synthetic review findings; model queue remains paused."""

import fcntl
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

from project_fixtures import adopt, task_request

from flowfield.adapters.git_workspace import git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import MilestoneCreate, ProjectSetup, TaskPreparation, Workspace
from flowfield.delivery_models import DeliveryConfigure
from flowfield.execution_models import SettingsEdit, WorkerResult
from flowfield.milestone_delivery import MilestoneDeliveries
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.milestones import Milestones
from flowfield.result_models import ResultReview
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.supervisor import Supervisor
from flowfield.verification_plans import VerificationPlanEdit, VerificationPlans


def seed(directory: Path, kind: str):
    def wait(read, status):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            value = read()
            if value.status == status:
                return value
            time.sleep(0.05)
        raise AssertionError(f"Service did not reach {status}: {value}")

    store = Workspace(directory)
    project = f"combined-{kind}"
    repo = directory / project
    adopt(
        store,
        ProjectSetup(
            path=str(repo),
            id=project,
            task_prefix={"ready": "CBR", "failed": "CBF", "question": "CBQ"}[kind],
        ),
    )
    git(repo, "init", "-b", "main")
    (repo / ".gitignore").write_text(".flowfield/\n")
    (repo / "README.md").write_text("Combined review browser fixture\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-m",
        "Existing repository",
    )
    service = Supervisor(store)
    from flowfield.integration_models import IntegrationConfig

    service.integrations.configure(
        project,
        IntegrationConfig(
            expected_revision=1, target_branch="main", checks=["test -f feature.txt"]
        ),
    )
    service.execution.configure(
        project, SettingsEdit(expected_revision=1, selection=AgentChoice(model="fixture"))
    )
    Reviewers(store).configure(project, "task", ReviewerEdit(expected_revision=1, enabled=False))
    Reviewers(store).configure(
        project,
        "milestone",
        ReviewerEdit(expected_revision=1, enabled=True, selection=AgentChoice(model="fixture")),
    )
    milestone = Milestones(store).create(
        project,
        MilestoneCreate(
            title=f"Combined review {kind}",
            body="Review the delivered outcome and its exact promotion candidate.",
            acceptance_criteria="The combined feature meets its agreed outcome.",
        ),
    )
    MilestoneDeliveries(store).configure(
        project, milestone.id, DeliveryConfigure(expected_revision=1, project_id=project)
    )
    VerificationPlans(store).save(
        project,
        milestone.id,
        VerificationPlanEdit(
            expected_revision=1,
            milestone_revision=1,
            checks=["Combined feature is available and correct"],
            instructions="Inspect the combined feature and exact configured validation evidence.",
        ),
    )
    task = store.create_task(
        project,
        task_request(
            title="Implement the combined feature",
            body="Deliver the feature text.",
            milestone_id=milestone.id,
            status="up_next",
            preparation=TaskPreparation(completion="code"),
        ),
    )
    settings = service.execution.settings(project).model_copy(update={"enabled": True})
    head = service.integrations.head(project, task.id)
    with patch.object(service.execution, "_settings", return_value=settings):
        run = service.execution.claim(project, head, {head: set()})
    assert run
    service.execution.started(project, run.id)
    env = LocalHost(os.environ).prepare(store.directory, repo, run.id, head)
    (env.checkout / "feature.txt").write_text("Useful combined feature\n")
    commit, _ = env.snapshot(head)
    service.execution.finish(
        project,
        run.id,
        "in_review",
        result=WorkerResult(summary="Feature implemented", checks="Configured check passed"),
        commit=commit,
    )
    # The running browser service owns validation/delivery. A second processor
    # would compete for its Git transaction and mistake active work for recovery.
    result = wait(lambda: service.results.page(project, task.id).items[0], "ready")
    service.results.review(
        project,
        result.id,
        ResultReview(
            expected_revision=result.revision,
            candidate_commit=result.candidate_commit,
            action="approve",
        ),
    )
    wait(lambda: service.results.get(project, result.id), "delivered")
    # The delivered receipt is saved just before the service releases its Git lock.
    with (repo / ".git" / "flowfield-integration.lock").open("ab") as transaction:
        fcntl.flock(transaction, fcntl.LOCK_EX)
    reviews = service.milestone_review_execution.reviews
    with patch("flowfield.milestone_reviews.read_project", return_value=settings):
        value = reviews.prepare(project, milestone.id)
        wait(lambda: MilestoneVerifications(store).get(project, value.id), "verifying")
        review = reviews.enqueue(project, value.id)
        review = reviews.claim(project, review.id, review.revision)
        review = reviews.begin_attempt(project, review.id)
        review = reviews.started(project, review.id, review.revision)
        decision = {
            "action": "report",
            "summary": "Combined outcome verified",
            "evidence": "Model-free browser fixture: exact candidate and real check evidence.",
            "report": {
                "verification_id": value.id,
                "coverage": "complete",
                "coverage_notes": "All criteria covered by this synthetic report.",
                "findings": [
                    {
                        "check": 1,
                        "verdict": "passed",
                        "evidence": "The feature file exists and the configured check passes.",
                    }
                ],
            },
        }
        if kind == "question":
            decision = {
                "action": "ask_human",
                "summary": "Clarify the expected result text",
                "evidence": "The recorded requirement does not choose the final wording.",
                "request": "Confirm the wording or reconcile the criterion with the Coordinator.",
            }
        reviews.save_decision(project, review.id, review.attempt_id, decision)
        reviews.finish_attempt(
            project,
            review.id,
            review.attempt_id,
            cleanup_confirmed=True,
            problem="Reviewer setup unavailable; install the selected harness and retry."
            if kind == "failed"
            else None,
        )
    value = MilestoneVerifications(store).get(project, value.id)
    if kind == "ready":
        from flowfield.conversation import Conversation
        from flowfield.replies import Replies
        from flowfield.reply_models import MilestoneObservationBinding, ReplyBinding, ReplyCreate

        gate = Conversation(store).eligibility(project, task.id)
        Replies(store).submit(
            project,
            task.id,
            ReplyCreate(
                action="observation",
                binding=ReplyBinding.model_validate(
                    gate.model_dump(include=set(ReplyBinding.model_fields))
                ),
                milestone=MilestoneObservationBinding(
                    verification_id=value.id,
                    verification_revision=value.revision,
                    candidate_commit=value.promotion.candidate_commit,
                ),
                body="Human tested the complete pipeline: 19 cases passed.",
                author="coordinator:browser-fixture",
            ),
        )
    return {
        "workspace": project,
        "project": project,
        "milestone": milestone.key,
        "milestone_id": milestone.id,
        "verification": value.id,
        "review": review.id,
    }


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1]), sys.argv[2])))
