"""The inbox derives one human-oriented projection without changing work lifecycles."""

from pathlib import Path

from execution_fixtures import BASE, RESULT, fixture, result, validate_report

from flowfield.attention import attention_page
from flowfield.execution_models import RunAction, WorkerResult
from flowfield.questions import QuestionAnswer, QuestionCreate, Questions, QuestionWithdraw
from flowfield.result_models import ResultReview
from flowfield.results import Results


def test_mixed_attention_lifecycle_and_bounded_history(tmp_path: Path) -> None:
    execution = fixture(tmp_path, count=2)
    workspace = execution.workspace
    questions = Questions(workspace)
    first = result(execution, partial=True)
    first_version = Results(workspace).page("harbor", first.task_id).items[0]
    question = questions.ask(
        "harbor",
        QuestionCreate(
            id="scope", question="Which scope?", context="x" * 8000, recommendation="Local only"
        ),
    )

    def page(column, offset=0, limit=20):
        value = attention_page(workspace, "harbor", column, offset, limit)
        if column == "action":
            assert workspace.board("harbor").needs_you_count == value.total
        return value

    current = page("action")
    assert {item.id for item in current.items} == {first_version.id, question.id}
    assert "x" * 100 not in current.model_dump_json()
    answered = questions.answer(
        "harbor", question.id, QuestionAnswer(expected_revision=1, answer="Local only")
    )
    assert [item.id for item in page("waiting").items] == [question.id]
    assert page("action").total == 1
    history = attention_page(workspace, "harbor", "history", limit=1, include_answered=True)
    assert question.id in {item.id for item in history.items}
    assert history.total == 1
    assert page("history").total == 0
    results = Results(execution.workspace)
    version = results.page("harbor", first.task_id).items[0]
    results.review(
        "harbor",
        version.id,
        ResultReview(
            expected_revision=version.revision,
            candidate_commit=RESULT,
            action="request_changes",
            note="Add a check",
            author="human",
        ),
    )
    assert {item.id for item in page("waiting").items} == {first_version.id, question.id}
    successor = execution.claim("harbor", BASE, {BASE: set()})
    assert successor and successor.predecessor_id == first.id
    assert {item.id for item in page("waiting").items} == {successor.id, question.id}
    assert [item.id for item in page("history").items] == [first_version.id]
    successor = execution.finish(
        "harbor",
        successor.id,
        "in_review",
        commit="c" * 40,
        result=WorkerResult(summary="Revised", checks="Checked"),
    )
    validate_report(execution)
    successor_version = Results(workspace).page("harbor", successor.task_id).items[0]
    assert page("action").total == 0
    assert successor_version.status == "delivered"
    questions.withdraw(
        "harbor",
        question.id,
        QuestionWithdraw(expected_revision=answered.revision, reason="No longer needed"),
    )
    history = page("history", limit=1)
    assert history.total == 3 and history.next_offset == 1
    all_items = history.items + page("history", offset=1, limit=2).items
    assert len({item.id for item in all_items}) == 3
    accepted = next(item for item in all_items if item.status == "delivered")
    assert accepted.kind == "result"
    assert page("action").total == page("waiting").total == 0
    # A fresh initial worker stays on the board, not in the human attention queue.
    initial = execution.claim("harbor", BASE, {BASE: set()})
    assert initial and initial.predecessor_id is None
    assert page("waiting").total == 0
    failed = execution.finish("harbor", initial.id, "failed", problem="Fixture failure")
    assert page("action").items[0].kind == "intervention"
    execution.retry("harbor", failed.id, RunAction(expected_revision=failed.revision))
    assert page("action").total == 0
    assert page("waiting").total == 0
    assert failed.id in {item.id for item in page("history").items}
    # Paused scheduling leaves the failed evidence intact without a stale callout.
    assert execution.get("harbor", failed.id).status == "failed"


def test_attention_http_limits_and_project_scope(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from flowfield.api import create_app
    from flowfield.supervisor import Supervisor

    async def no_scheduler(self):
        pass

    monkeypatch.setattr(Supervisor, "_schedule", no_scheduler)
    execution = fixture(tmp_path)
    questions = Questions(execution.workspace)
    for index in range(3):
        questions.ask(
            "harbor",
            QuestionCreate(
                id=f"q-{index}",
                question="Which scope?",
                context="Only local",
                recommendation="Small",
            ),
        )
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        path = "/api/workspaces/harbor/view/attention"
        first = client.get(path, params={"column": "action", "limit": 2}).json()
        assert first["total"] == 3 and first["next_offset"] == 2
        second = client.get(path, params={"column": "action", "offset": 2}).json()
        assert len(second["items"]) == 1 and second["next_offset"] is None
        assert first["items"][0]["id"] != second["items"][0]["id"]
        assert client.get(path, params={"column": "bogus"}).status_code == 422
        assert client.get(path, params={"column": "action", "limit": 101}).status_code == 422
        assert client.get(path, params={"column": "action", "offset": -1}).status_code == 422
        assert (
            client.get(path.replace("harbor", "other"), params={"column": "action"}).status_code
            == 404
        )
