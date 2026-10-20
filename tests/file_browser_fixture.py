"""Seed a disposable workspace file journey; no agents or enabled queues."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from file_delivery_fixtures import deliver, setup, task

from flowfield.agent_models import AgentChoice
from flowfield.execution import Execution
from flowfield.execution_models import WorkerResult
from flowfield.output_models import OutputSelection
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.task_reviews import ReviewVerdict, TaskReviews


def seed(directory):
    store, owner, scope = setup(directory)
    producer = task(store, scope)
    result = deliver(
        store,
        scope,
        producer.id,
        {
            "launch-report.txt": "Launch research\n\nThe pilot supports a focused launch.\n",
            "sources.csv": (
                'source,observations\r\n"Interviews, users",0012\r\n'
                '"Two\nlines","Said ""yes"""\r\n<img src=x>,=1+1\r\n'
            ),
            "large.csv": "source,value\n" + "entry,1\n" * 250,
            "malformed.csv": 'source,value\n"unterminated,1',
            "long.txt": "Long retained text for the available preview space.\n" * 200,
            "preview.js": (
                "const message = '<img src=x onerror=alert(1)>';\nconsole.log(message);\n"
            ),
            "preview.md": "# Launch notes\n\n- Read the report\n\n<script>alert(1)</script>\n",
            "bundle.bin": "Retained output without a supported inline preview.",
        },
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
        ReviewerEdit(expected_revision=1, enabled=True, selection=AgentChoice(model="fixture")),
        entity=assessed.id,
    )
    assessed_result = deliver(store, scope, assessed.id, {"assessment.txt": "Retained evidence"})
    reviews = TaskReviews(store)
    review = reviews.prepare(scope, assessed_result.id)
    reviews.stop(scope, review.id, review.revision, cleanup_confirmed=True)
    samples = task(store, scope, title="Retain supporting samples")
    deliver(
        store,
        scope,
        samples.id,
        {f"evidence/sample-{index:02}.txt": f"Sample {index}" for index in range(23)},
    )
    waiting = task(store, scope, title="Confirm the screenshot scope")
    Reviewers(store).configure(
        owner.id,
        ReviewerEdit(expected_revision=1, enabled=True, selection=AgentChoice(model="fixture")),
        entity=waiting.id,
    )
    waiting_result = deliver(
        store,
        scope,
        waiting.id,
        {f"evidence/sample-{index:02}.txt": f"Sample {index}" for index in range(23, 55)},
    )
    review = reviews.prepare(scope, waiting_result.id)
    settings = Execution(store).settings(scope).model_copy(update={"enabled": True})
    with patch("flowfield.task_reviews.read_workspace", return_value=settings):
        review = reviews.claim(scope, review.id, review.revision)
    review = reviews.begin_attempt(scope, review.id)
    review = reviews.started(scope, review.id, review.revision)
    reviews.save_decision(
        scope,
        review.id,
        review.attempt_id,
        ReviewVerdict(
            action="ask_human",
            summary="Confirm capture scope",
            evidence="The viewport is unspecified.",
            request="Which viewport should the screenshot use?",
        ).model_dump(),
    )
    reviews.finish_attempt(scope, review.id, review.attempt_id, cleanup_confirmed=True)
    return {
        "workspace": owner.id,
        "scope": scope,
        "task": producer.key,
        "result": result.id,
        "partial_task": unfinished.key,
        "partial_result": partial.id,
        "file": result.files[0].id,
        "review_task": assessed.key,
        "review_result": assessed_result.id,
        "waiting_task": waiting.key,
        "waiting_review": review.id,
        "waiting_question": reviews.get(scope, review.id).question_id,
    }


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1]))))
