"""Reusable isolated setup and scripted peers; no collected test cases."""

from execution_fixtures import BASE

from flowfield.execution_models import QueueEdit
from flowfield.questions import QuestionAnswer, QuestionCreate


def question(execution):
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    q = execution.ask_question(
        "harbor",
        run.id,
        QuestionCreate(
            task_id=run.task_id,
            question="Which rows?",
            context="Two product behaviors are possible.",
            recommendation="Use all filtered rows.",
            blocking_scope="Export scope",
        ),
    )
    return run, q


def queue(execution, enabled):
    settings = execution.settings("harbor")
    execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=enabled))


def answer(questions, q, value="All filtered rows"):
    return questions.answer(
        "harbor", q.id, QuestionAnswer(expected_revision=q.revision, answer=value)
    )
