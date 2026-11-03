"""Import pre-retirement question records for history and continuity tests."""

from typing import Any

from flowfield.questions import Question, QuestionCreate, Questions


def seed_question(questions: Questions, workspace_id: str, request: QuestionCreate) -> Question:
    with questions.workspace.connection(write=True, workspace_id=workspace_id) as db:
        return questions._ask(db, workspace_id, request)


def seed_follow_up(
    questions: Questions, workspace_id: str, identity: str, request: dict[str, Any]
) -> Question:
    with questions.workspace.connection(write=True, workspace_id=workspace_id) as db:
        current = questions._get(db, workspace_id, identity)
        changes = dict(request)
        revision = changes.pop("expected_revision")
        author = changes.pop("author", "human")
        question = questions._change(
            current, revision, author, **changes, status="open", answer=None
        )
        return questions._save(db, question, "Follow-up requested: " + question.question)
