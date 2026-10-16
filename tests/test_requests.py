"""Stable request references preserve source identity, scope and existing response ownership."""

import asyncio
from uuid import uuid4

import pytest
from execution_fixtures import fixture, result

from flowfield.agent_tools import workspace_scope
from flowfield.attention import attention_page
from flowfield.coordinator_context import freeze_view
from flowfield.coordinator_models import CoordinatorViewSelection
from flowfield.errors import ApplicationError
from flowfield.questions import QuestionAnswer, QuestionCreate, Questions, QuestionWithdraw
from flowfield.reads import PAGE_BYTES, ContextReads, size
from flowfield.requests import get_request
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_exact_request_survives_answer_and_absence_from_action_page(tmp_path):
    execution = fixture(tmp_path)
    ws = execution.workspace
    questions = Questions(ws)
    q = questions.ask(
        "harbor",
        QuestionCreate(id="ask", question="Which format?", context="Export", recommendation="CSV"),
    )
    item = get_request(ws, "harbor", "question", q.id, "harbor")
    assert item.request_href == "/workspaces/harbor/requests/question/ask?project_id=harbor"
    assert item.source_href == "/projects/harbor"
    assert (
        attention_page(ws, None, "action", workspace_id="harbor").items[0].request_href
        == item.request_href
    )
    questions.answer("harbor", q.id, QuestionAnswer(expected_revision=q.revision, answer="CSV"))
    assert not attention_page(ws, None, "action", workspace_id="harbor").items
    saved = get_request(ws, "harbor", "question", q.id, "harbor")
    assert saved.request_href == item.request_href and saved.status == "answered"
    read = ContextReads(ws, "http://localhost:8765").request("harbor", "question", q.id, "harbor")
    assert read["question"]["answer"] == "CSV"
    assert read["url"].endswith(item.request_href)
    questions.withdraw("harbor", q.id, QuestionWithdraw(expected_revision=2, reason="Superseded"))
    assert get_request(ws, "harbor", "question", q.id, "harbor").status == "withdrawn"


def test_scope_and_exact_source_family_are_checked(tmp_path):
    ws = fixture(tmp_path).workspace
    q = Questions(ws).ask(
        "harbor",
        QuestionCreate(id="ask", question="Which format?", context="Export", recommendation="CSV"),
    )
    foreign = Workspaces(ws).create(WorkspaceCreate(name="Elsewhere"))
    for workspace, kind, identity, project in [
        (foreign.id, "question", q.id, "harbor"),
        ("harbor", "result", q.id, "harbor"),
        ("harbor", "question", q.id, None),
        ("harbor", "question", "missing", "harbor"),
    ]:
        with pytest.raises(ApplicationError):
            get_request(ws, workspace, kind, identity, project)
    with ws.connection() as db:
        selection = CoordinatorViewSelection(
            surface="request", request_kind="question", request_id=q.id, project_id="harbor"
        )
        frozen = freeze_view(ws, db, "harbor", selection, None)
        assert frozen.request_title == q.question and frozen.request_status == "open"
        assert frozen.request_href.endswith("?project_id=harbor")
        with pytest.raises(ApplicationError):
            freeze_view(ws, db, "harbor", selection.model_copy(update={"surface": "inbox"}), None)


def test_result_and_attempt_links_survive_result_creation(tmp_path):
    execution = fixture(tmp_path)
    run = result(execution, partial=True)
    ws = execution.workspace
    result_item = attention_page(ws, "harbor", "action").items[0]
    exact = get_request(ws, "harbor", "result", result_item.id, "harbor")
    assert exact.id == result_item.id and exact.source_href.endswith(
        f"/tasks/{result_item.task_key}"
    )
    attempt = get_request(ws, "harbor", "attempt", run.id, "harbor")
    assert attempt.id == run.id and "/requests/attempt/" in attempt.request_href


def test_scoped_coordinator_can_reference_requests_without_extra_authority(tmp_path):
    ws = fixture(tmp_path).workspace
    q = Questions(ws).ask(
        "harbor",
        QuestionCreate(
            id=uuid4().hex, question="Which format?", context="Export", recommendation="CSV"
        ),
    )

    async def check():
        grant = await workspace_scope(Supervisor(ws), "harbor")
        listed = await grant.call("list_requests", {})
        assert not listed.isError and listed.structuredContent["items"][0]["request_href"]
        exact = await grant.call(
            "get_request", {"kind": "question", "request_id": q.id, "project_id": "harbor"}
        )
        assert not exact.isError and exact.structuredContent["id"] == q.id
        # Workspace requests have no project; omission reaches the source lookup.
        missing = await grant.call("get_request", {"kind": "milestone", "request_id": "missing"})
        assert missing.structuredContent["error"]["code"] == "request_not_found"
        foreign = await grant.call(
            "get_request", {"kind": "question", "request_id": q.id, "project_id": "foreign"}
        )
        assert foreign.isError
        required = await grant.call("get_task", {"task_id": "task"})
        assert required.structuredContent["error"]["code"] == "project_required"
        grant.revoke()

    asyncio.run(check())


def test_request_pages_bound_bytes_without_skipping_records(tmp_path):
    ws = fixture(tmp_path).workspace
    questions = Questions(ws)
    for number in range(50):
        questions.ask(
            "harbor",
            QuestionCreate(
                id=f"ask-{number}", question="界" * 200, context="Export", recommendation="CSV"
            ),
        )
    reads = ContextReads(ws)
    offset = 0
    seen = set()
    while True:
        value = reads.requests("harbor", limit=50, offset=offset)
        assert size(value) <= PAGE_BYTES
        identities = {item["id"] for item in value["items"]}
        assert not seen & identities
        seen |= identities
        if value["next_offset"] is None:
            break
        assert value["next_offset"] > offset
        offset = value["next_offset"]
    assert len(seen) == 50
