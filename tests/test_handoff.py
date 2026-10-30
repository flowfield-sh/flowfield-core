"""A returning coordinator gets a selected, revision-bound continuation checkpoint."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from project_fixtures import adopt, task_request
from pydantic import ValidationError

from flowfield.activity import ActivityCreate
from flowfield.application import ProjectSetup, TaskEdit, Workspace
from flowfield.errors import ApplicationError
from flowfield.reads import PAGE_BYTES, ContextReads, size


def test_handoff_selection_conflicts_retry_and_restart(tmp_path: Path) -> None:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    service.create_task("harbor", task_request(id="one", title="Catalog"))
    first = ActivityCreate(
        id="first",
        task_id="T-1",
        kind="handoff",
        body="catalog.py; tests pass; add CLI next.",
        expected_task_revision=1,
        supersedes=None,
    )
    service.add_activity("harbor", first)
    service.add_activity("harbor", ActivityCreate(task_id="one", body="An unrelated later note."))
    assert ContextReads(service).task("harbor", "one")["handoff"]["id"] == "first"
    service.edit_task(
        "harbor", "one", TaskEdit(expected_revision=1, body="Also reject duplicates.")
    )
    assert ContextReads(service).task("harbor", "one")["handoff"]["needs_recheck"]
    assert service.add_activity("harbor", first).id == "first"  # retry never reselects
    with pytest.raises(ApplicationError, match="already used"):
        service.add_activity("harbor", first.model_copy(update={"expected_task_revision": 2}))
    with pytest.raises(ApplicationError, match="changed"):
        service.add_activity("harbor", first.model_copy(update={"id": "stale"}))

    def replace(identity: str):
        try:
            return service.add_activity(
                "harbor",
                ActivityCreate(
                    id=identity,
                    task_id="one",
                    kind="handoff",
                    body="🦉" * 40000,
                    expected_task_revision=2,
                    supersedes="first",
                ),
            )
        except ApplicationError as error:
            assert error.code == "handoff_conflict"
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(replace, ["second", "third"]))
    winners = [item for item in results if item]
    assert len(winners) == 1
    restarted = Workspace(tmp_path / "state")
    detail = ContextReads(restarted).task("harbor", "one")
    assert detail["handoff"]["id"] == winners[0].id
    assert detail["latest_update"]["body_in"] == "handoff"
    assert not detail["handoff"]["needs_recheck"]
    assert detail["handoff"]["truncated_fields"]["body"]
    assert size(detail) < PAGE_BYTES
    assert restarted.activity_entry("harbor", "first").superseded_by == winners[0].id
    assert ContextReads(restarted).text("harbor", "activity", winners[0].id)["next_offset"] == 4000
    for values in ({}, {"expected_task_revision": 2}, {"supersedes": None}):
        with pytest.raises(ValidationError):
            ActivityCreate(task_id="one", kind="handoff", body="Incomplete", **values)


def test_briefing_prioritizes_answers_and_exposes_omissions(tmp_path: Path) -> None:
    from flowfield.application import TaskPriority
    from flowfield.questions import QuestionAnswer, QuestionCreate, Questions

    service = Workspace(tmp_path / "state")
    root = tmp_path / "harbor"
    adopt(service, ProjectSetup(path=str(root)))
    (root / "AGENTS.md").write_text("Use unittest.")
    for number in range(5):
        service.create_task(
            "harbor",
            task_request(
                id=f"t{number}",
                title=f"Task {number}",
                body="Report findings",
                status="up_next",
                completion="report",
            ),
        )
    service.prioritize_task(
        "harbor", "t4", TaskPriority(expected_revision=1, status="up_next", before_id="t0")
    )
    reads = ContextReads(service)
    assert reads.overview("harbor")["recommendation"]["id"] == "t4"
    assert reads.overview("harbor")["recommendation"]["task_key"] == "T-5"
    questions = Questions(service)
    for number in range(5):
        questions.ask(
            "harbor",
            QuestionCreate(
                id=f"q{number}",
                question="Encoding?",
                context="Export files",
                recommendation="UTF-8",
            ),
        )
    questions.answer("harbor", "q4", QuestionAnswer(expected_revision=1, answer="UTF-8"))
    board = reads.overview("harbor")
    assert board["recommendation"]["action"] == "apply_answer"
    assert board["recommendation"]["id"] == "q4"
    assert board["attention"]["open"]["omitted_count"] == 1
    assert board["columns"][1]["omitted_count"] == 2
    assert board["observed_at"] and board["execution"] == "managed_local"
    assert size(board) < PAGE_BYTES
    questions.ask(
        "harbor",
        QuestionCreate(
            id="shared",
            affected_task_ids=["t0", "t1"],
            question="Shared?",
            context="Both tasks",
            recommendation="Yes",
        ),
    )
    shared = [
        e
        for e in reads.overview("harbor")["recent_changes"]["items"]
        if e["question_id"] == "shared"
    ]
    assert len(shared) == 1 and shared[0]["task_id"] is None
