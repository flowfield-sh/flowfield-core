"""Canonical input, atomic application, and bounded coordinator reads."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lifecycle_fixtures import run_report
from project_fixtures import adopt, task_request
from questions_fixtures import ask, setup

from flowfield.api import create_app
from flowfield.application import ProjectSetup, TaskEdit, Workspace
from flowfield.errors import ApplicationError
from flowfield.questions import (
    QuestionAnswer,
    QuestionApply,
    QuestionCreate,
    QuestionFollowUp,
    Questions,
    QuestionWithdraw,
)
from flowfield.reads import PAGE_BYTES, ContextReads, size


def test_answer_is_not_application_and_other_gates_survive(tmp_path: Path) -> None:
    workspace, questions = setup(tmp_path)
    original = workspace.task("harbor", "T-2")
    question = ask(questions)
    assert ask(questions) == question  # same caller-supplied ID is safe to retry
    assert workspace.task("harbor", "T-2").revision == original.revision
    answered = questions.answer(
        "harbor",
        question.id,
        QuestionAnswer(expected_revision=1, answer="All filtered rows, capped at 10,000."),
    )
    assert (
        questions.answer(
            "harbor", question.id, QuestionAnswer(expected_revision=2, answer=answered.answer or "")
        )
        == answered
    )
    task = workspace.task("harbor", "T-2")
    assert task.body == "Export CSV" and task.blocking_questions[0].status == "answered"
    with pytest.raises(ApplicationError, match="Blocked by"):
        run_report(workspace, "harbor", "T-2", expected_revision=1, status="in_progress")
    ask(questions, "format")
    resolved = questions.apply(
        "harbor",
        question.id,
        QuestionApply(
            expected_revision=2,
            expected_task_revision=1,
            body="Export all filtered rows, capped at 10,000.",
            decision="Export all filtered rows with a 10,000-row cap.",
            author="agent",
        ),
    )
    task = workspace.task("harbor", "T-2")
    assert resolved.status == "applied" and resolved.applied_task_revision == 2
    assert task.body.endswith("10,000.") and task.status == "up_next"
    assert [q.id for q in task.blocking_questions] == ["format"]
    assert [t.key for t in task.blocked_by] == ["T-1"]
    assert any(
        entry.question_id == "scope"
        for entry in workspace.activity("harbor", task_id=task.id).items
    )
    assert questions.list("harbor")["needs_you_count"] == 1
    questions.withdraw(
        "harbor",
        "format",
        QuestionWithdraw(expected_revision=1, reason="Covered by the existing CSV standard."),
    )
    assert workspace.task("harbor", "T-2").readiness == "blocked"  # dependency still applies
    run_report(workspace, "harbor", "T-1", expected_revision=1, status="done", completion="report")
    assert workspace.task("harbor", "T-2").readiness == "draft"
    restarted = Workspace(tmp_path / "state")
    assert Questions(restarted).get("harbor", "scope") == resolved
    assert restarted.task("harbor", "T-2").body == task.body
    assert answered.answer == questions.get("harbor", "scope", revision=2).answer


def test_stale_application_and_competing_answers_do_not_lose_work(tmp_path: Path) -> None:
    workspace, questions = setup(tmp_path)
    question = ask(questions)

    def answer(value: str):
        try:
            return questions.answer(
                "harbor", question.id, QuestionAnswer(expected_revision=1, answer=value)
            )
        except ApplicationError as error:
            assert error.code == "revision_conflict"
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(answer, ["All rows", "Visible page"]))
    assert len([r for r in results if r]) == 1
    workspace.edit_task(
        "harbor", "T-2", TaskEdit(expected_revision=1, body="Concurrent requirement")
    )
    prior = workspace.activity("harbor", task_id="T-2")
    with pytest.raises(ApplicationError, match="stale"):
        questions.apply(
            "harbor",
            question.id,
            QuestionApply(
                expected_revision=2, expected_task_revision=1, body="Lost update", decision="Choice"
            ),
        )
    assert workspace.activity("harbor", task_id="T-2") == prior
    assert questions.get("harbor", question.id).status == "answered"
    questions.answer(
        "harbor", question.id, QuestionAnswer(expected_revision=2, answer="New conclusion")
    )
    with pytest.raises(ApplicationError, match="stale"):
        questions.apply(
            "harbor",
            question.id,
            QuestionApply(
                expected_revision=2, expected_task_revision=2, body="Lost update", decision="Choice"
            ),
        )
    assert workspace.task("harbor", "T-2").body == "Concurrent requirement"


def test_followup_retains_answers_and_scope_without_duplicate_requests(tmp_path: Path) -> None:
    workspace, questions = setup(tmp_path)
    original = ask(questions)
    questions.answer("harbor", original.id, QuestionAnswer(expected_revision=1, answer="Cap it."))
    followup = questions.follow_up(
        "harbor",
        original.id,
        QuestionFollowUp(
            expected_revision=2,
            question="What should happen above the cap?",
            context="The previous answer leaves truncation ambiguous.",
            recommendation="Reject the export with an explanation.",
        ),
    )
    assert followup.status == "open" and followup.answer is None
    assert followup.blocking_scope == original.blocking_scope
    assert questions.get("harbor", original.id, 2).answer == "Cap it."
    assert len(questions.list("harbor")["items"]) == 1
    assert questions.history("harbor", original.id, before=3)["items"][0]["answer"] == "Cap it."
    with pytest.raises(ApplicationError, match="Save an answer"):
        questions.apply(
            "harbor",
            original.id,
            QuestionApply(expected_revision=3, expected_task_revision=1, decision="Not answered"),
        )
    with pytest.raises(ApplicationError, match="pending questions"):
        workspace.edit_task("harbor", "T-2", TaskEdit(expected_revision=1, archived=True))
    questions.withdraw(
        "harbor",
        original.id,
        QuestionWithdraw(expected_revision=3, reason="Export no longer planned."),
    )
    with pytest.raises(ApplicationError, match="resolved"):
        questions.answer(
            "harbor", original.id, QuestionAnswer(expected_revision=4, answer="Too late")
        )


def test_nonblocking_input_and_question_only_readiness_are_consistent(tmp_path: Path) -> None:
    workspace, questions = setup(tmp_path)
    run_report(workspace, "harbor", "T-1", expected_revision=1, status="done", completion="report")
    question = ask(questions, blocking=False)
    assert workspace.task("harbor", "T-2").readiness == "draft"
    run_report(workspace, "harbor", "T-2", expected_revision=1, status="in_progress")
    gate = ask(questions, "blocking")
    reads = ContextReads(workspace)
    assert reads.task("harbor", "T-2")["blocking_question_count"] == 1
    assert reads.tasks("harbor", readiness="blocked")["items"][0]["key"] == "T-2"
    assert reads.overview("harbor")["needs_you_count"] == 2
    questions.answer(
        "harbor", gate.id, QuestionAnswer(expected_revision=1, answer="Use the existing contract.")
    )
    questions.apply(
        "harbor",
        gate.id,
        QuestionApply(
            expected_revision=2,
            expected_task_revision=workspace.task("harbor", "T-2").revision,
            decision="Existing Description already covers this choice.",
        ),
    )
    assert workspace.task("harbor", "T-2").readiness == "ready"
    assert questions.get("harbor", question.id).status == "open"


def test_scope_pagination_limits_and_http_validation(tmp_path: Path) -> None:
    workspace, questions = setup(tmp_path)
    for number in range(24):
        questions.ask(
            "harbor",
            QuestionCreate(
                id=f"q{number}",
                task_id="T-2",
                question=f"Question {number}",
                context="x" * 8000,
                recommendation="y" * 8000,
                blocking_scope="z" * 8000,
            ),
        )
    first = questions.list("harbor", limit=10)
    assert size(first) < PAGE_BYTES and len(first["items"]) == 10
    second = questions.list("harbor", after=first["next_cursor"], limit=20)
    assert len(second["items"]) == 14
    detail = ContextReads(workspace).task("harbor", "T-2")
    assert detail["blocking_question_count"] == 24 and len(detail["blocking_questions"]) == 5
    assert size(detail) < PAGE_BYTES
    text = ContextReads(workspace).text("harbor", "question", "q0", field="context", revision=1)
    assert text["next_offset"] == 4000
    adopt(workspace, ProjectSetup(path=str(tmp_path / "other")))
    with pytest.raises(ApplicationError, match="not found"):
        questions.get("other", "q0")
    with pytest.raises(ApplicationError, match="not found"):
        questions.list("other", task_id="T-2")
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        assert (
            client.post(
                "/api/workspaces/harbor/questions/q0/answer",
                json={"expected_revision": 1, "answer": "  "},
            ).status_code
            == 422
        )
        assert client.get("/api/workspaces/harbor/questions?limit=51").status_code == 400
        assert (
            client.post(
                "/api/workspaces/harbor/questions",
                json={
                    "task_id": "T-1",
                    "question": "Done?",
                    "context": "Context",
                    "recommendation": "Recommendation",
                },
            ).status_code
            == 201
        )


def test_retract_answer_preserves_history_and_gates_application(tmp_path: Path) -> None:
    from flowfield.questions import AnswerRetract

    workspace, questions = setup(tmp_path)
    ask(questions)
    saved = questions.answer(
        "harbor", "scope", QuestionAnswer(expected_revision=1, answer="Visible page")
    )
    reopened = questions.retract_answer(
        "harbor", "scope", AnswerRetract(expected_revision=saved.revision)
    )
    assert reopened.status == "open" and reopened.answer is None
    assert reopened.answer_count == 1
    assert questions.get("harbor", "scope", saved.revision).answer == "Visible page"
    assert workspace.task("harbor", "T-2").blocking_questions[0].status == "open"
    with pytest.raises(ApplicationError):
        questions.apply(
            "harbor",
            "scope",
            QuestionApply(
                expected_revision=saved.revision, expected_task_revision=1, decision="Stale answer"
            ),
        )
    with pytest.raises(ApplicationError):
        questions.retract_answer("harbor", "scope", AnswerRetract(expected_revision=saved.revision))
    again = questions.answer(
        "harbor",
        "scope",
        QuestionAnswer(expected_revision=reopened.revision, answer="All filtered rows"),
    )
    questions.apply(
        "harbor",
        "scope",
        QuestionApply(
            expected_revision=again.revision, expected_task_revision=1, decision="All rows"
        ),
    )
    with pytest.raises(ApplicationError, match="unapplied"):
        questions.retract_answer(
            "harbor", "scope", AnswerRetract(expected_revision=again.revision + 1)
        )


def project_question(questions: Questions, targets: list[str] | None = None):
    return questions.ask(
        "harbor",
        QuestionCreate(
            id="project-scope",
            affected_task_ids=targets or [],
            question="Which output convention?",
            context="Exports must agree across features.",
            recommendation="Use UTF-8.",
            blocking_scope="Output contract" if targets else None,
        ),
    )


def test_project_input_applies_once_without_placeholder_task(tmp_path: Path) -> None:
    workspace, questions = setup(tmp_path)
    question = project_question(questions)
    assert question.task_id is None and question.task_key is None
    assert questions.list("harbor", task_id="T-1")["items"] == []
    questions.answer("harbor", question.id, QuestionAnswer(expected_revision=1, answer="UTF-8"))
    result = questions.apply(
        "harbor",
        question.id,
        QuestionApply(
            expected_revision=2,
            expected_workspace_revision=1,
            description="Offline UTF-8 exports.",
            decision="Use UTF-8 across exports.",
        ),
    )
    assert result.applied_workspace_revision == 2
    assert result.applied_task_revisions == {}
    from flowfield.workspaces import Workspaces

    assert Workspaces(workspace).get("harbor").description == "Offline UTF-8 exports."
    assert workspace.task("harbor", "T-1").revision == 1
    history = [
        e
        for e in workspace.activity("harbor", kind="event").items
        if e.body == "Use UTF-8 across exports."
    ]
    assert len(history) == 1 and history[0].task_id is None
    assert history[0].question_id == question.id


def test_project_input_targets_and_atomic_stale_protection(tmp_path: Path) -> None:
    from flowfield.questions import QuestionTaskUpdate

    workspace, questions = setup(tmp_path)
    workspace.create_task(
        "harbor", task_request(id="independent", title="Independent", status="up_next")
    )
    question = project_question(questions, ["T-1", "serializer", "T-2"])
    assert len(question.affected_task_ids) == 2
    assert project_question(questions, ["T-2", "T-1"]) == question
    assert workspace.task("harbor", "T-3").readiness == "draft"
    assert questions.list("harbor", task_id="T-1")["items"][0]["id"] == question.id
    assert workspace.task("harbor", "T-1").blocking_questions[0].id == question.id
    with pytest.raises(ApplicationError):
        workspace.edit_task("harbor", "T-1", TaskEdit(expected_revision=1, archived=True))
    questions.answer("harbor", question.id, QuestionAnswer(expected_revision=1, answer="UTF-8"))
    assert workspace.task("harbor", "T-1").blocking_questions[0].status == "answered"
    workspace.edit_task(
        "harbor", "T-2", TaskEdit(expected_revision=1, body="Preserve concurrent edit")
    )
    request = QuestionApply(
        expected_revision=2,
        expected_workspace_revision=1,
        description="UTF-8 exports",
        task_updates=[
            QuestionTaskUpdate(task_id="T-1", expected_revision=1, body="UTF-8 serializer"),
            QuestionTaskUpdate(task_id="T-2", expected_revision=1, body="UTF-8 download"),
        ],
        decision="All outputs use UTF-8.",
    )
    prior = workspace.activity("harbor")
    with pytest.raises(ApplicationError, match="stale"):
        questions.apply("harbor", question.id, request)
    assert workspace.activity("harbor") == prior
    assert workspace.project("harbor").revision == 1
    assert workspace.task("harbor", "T-1").revision == 1
    assert workspace.task("harbor", "T-2").body == "Preserve concurrent edit"
    assert questions.get("harbor", question.id).status == "answered"
    request.task_updates[1].expected_revision = 2
    request.expected_workspace_revision = 99
    with pytest.raises(ApplicationError, match="stale"):
        questions.apply("harbor", question.id, request)
    assert workspace.task("harbor", "T-1").revision == 1
    request.expected_workspace_revision = 1
    applied = questions.apply("harbor", question.id, request)
    assert applied.applied_task_revisions == {"serializer": 2, "download": 3}
    assert workspace.task("harbor", "T-1").readiness == "draft"
    assert workspace.task("harbor", "T-2").readiness == "blocked"
    assert not workspace.task("harbor", "T-2").blocking_questions
    assert (
        sum(
            e.body == "All outputs use UTF-8."
            for e in workspace.activity("harbor", kind="event").items
        )
        == 1
    )


def test_project_question_rejects_ambiguous_scope_and_incomplete_application(
    tmp_path: Path,
) -> None:
    from pydantic import ValidationError

    from flowfield.questions import QuestionTaskUpdate

    workspace, questions = setup(tmp_path)
    fields = dict(question="Output?", context="Shared", recommendation="UTF-8")
    with pytest.raises(ValidationError):
        QuestionCreate(**fields, blocking_scope="Everything")
    with pytest.raises(ValidationError):
        QuestionCreate(**fields, task_id="T-1", affected_task_ids=["T-2"])
    with pytest.raises(ApplicationError):
        project_question(questions, ["missing"])
    question = project_question(questions, ["T-1"])
    questions.answer("harbor", question.id, QuestionAnswer(expected_revision=1, answer="UTF-8"))
    for updates in (
        [],
        [QuestionTaskUpdate(task_id="T-2", expected_revision=1)],
        [
            QuestionTaskUpdate(task_id="T-1", expected_revision=1),
            QuestionTaskUpdate(task_id="serializer", expected_revision=1),
        ],
    ):
        with pytest.raises(ApplicationError):
            questions.apply(
                "harbor",
                question.id,
                QuestionApply(
                    expected_revision=2,
                    expected_workspace_revision=1,
                    task_updates=updates,
                    decision="UTF-8",
                ),
            )
        assert questions.get("harbor", question.id).status == "answered"
    questions.withdraw(
        "harbor", question.id, QuestionWithdraw(expected_revision=2, reason="Unneeded")
    )
    assert workspace.task("harbor", "T-1").readiness == "draft"
    workspace.edit_task("harbor", "T-2", TaskEdit(expected_revision=1, dependencies=[]))
    workspace.edit_task("harbor", "T-1", TaskEdit(expected_revision=1, archived=True))
    with pytest.raises(ApplicationError, match="active tasks"):
        questions.ask("harbor", QuestionCreate(**fields, affected_task_ids=["T-1"]))
