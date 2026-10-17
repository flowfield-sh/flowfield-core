"""Seed a disposable workspace file journey; no agents or enabled queues."""

import json
import sys
from pathlib import Path

from file_delivery_fixtures import deliver, setup, task

from flowfield.agent_models import AgentChoice
from flowfield.application import MilestoneCreate
from flowfield.execution_models import WorkerResult
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.milestones import Milestones
from flowfield.output_models import OutputSelection
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.task_reviews import TaskReviews
from flowfield.verification_models import VerificationPrepare, VerificationReport


def seed(directory):
    store, owner, scope = setup(directory)
    milestone = Milestones(store).create(
        owner.id,
        MilestoneCreate(
            title="Launch research",
            body="A concise market report with its source data.",
            acceptance_criteria="Explain the findings and provide the supporting dataset.",
        ),
    )
    producer = task(store, scope, milestone_id=milestone.id)
    result = deliver(
        store,
        scope,
        producer.id,
        {
            "launch-report.txt": "Launch research\n\nThe pilot supports a focused launch.\n",
            "sources.csv": "source,observations\nInterviews,12\nPilot teams,4\n",
        },
    )
    verifier = task(
        store,
        scope,
        title="Verify launch research",
        milestone_id=milestone.id,
        dependencies=[producer.key],
    )
    verification = MilestoneVerifications(store).prepare(
        owner.id,
        milestone.id,
        VerificationPrepare(
            expected_revision=milestone.revision,
            project_id=scope,
            task_id=verifier.id,
            expected_task_revision=verifier.revision,
            checks=["The report is supported by the dataset"],
        ),
    )
    deliver(
        store,
        scope,
        verifier.id,
        report=WorkerResult(
            summary="The report and dataset cover the agreed research outcome.",
            checks="Read both retained files.",
            verification=VerificationReport(
                verification_id=verification.id,
                findings=[
                    {
                        "check": 1,
                        "verdict": "passed",
                        "evidence": "The report cites the interviews and pilot data.",
                    }
                ],
                coverage="complete",
                coverage_notes="All requested findings have supporting data.",
                output_ids=[file.id for file in result.files],
            ),
        ),
    )
    unfinished = task(store, scope, title="Prepare a launch checklist")
    partial = deliver(
        store,
        scope,
        unfinished.id,
        {"checklist-draft.txt": "Confirm launch date\n"},
        WorkerResult(
            outcome="partial",
            summary="The checklist draft needs launch dates.",
            checks="Checked the draft for missing dates.",
            remaining_work="Confirm dates and finish the checklist.",
            files=[
                OutputSelection(
                    path="checklist-draft.txt",
                    description="Working draft; dates are still missing.",
                )
            ],
        ),
    )
    assessed = task(store, scope, title="Review retained findings")
    Reviewers(store).configure(
        owner.id,
        "task",
        ReviewerEdit(expected_revision=1, enabled=True, selection=AgentChoice(model="fixture")),
        project=scope,
        entity=assessed.id,
    )
    assessed_result = deliver(store, scope, assessed.id, {"assessment.txt": "Retained evidence"})
    reviews = TaskReviews(store)
    review = reviews.prepare(scope, assessed_result.id)
    reviews.stop(scope, review.id, review.revision, cleanup_confirmed=True)
    return {
        "workspace": owner.id,
        "scope": scope,
        "task": producer.key,
        "result": result.id,
        "milestone": milestone.key,
        "verification": verification.id,
        "partial_task": unfinished.key,
        "partial_result": partial.id,
        "file": result.files[0].id,
        "review_task": assessed.key,
        "review_result": assessed_result.id,
    }


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1]))))
