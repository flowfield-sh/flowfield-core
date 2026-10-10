"""The single composer shares exact, transactional bindings with managed workers."""

import asyncio
import json

import pytest
from project_fixtures import task_request
from test_execution import BASE, fixture, result
from test_input_continuation import question
from test_results import approve, current
from test_results import fixture as result_fixture

from flowfield.application import TaskEdit, TaskPublish, Workspace
from flowfield.conversation import Conversation
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, SettingsEdit
from flowfield.replies import Replies
from flowfield.reply_models import ReplyBinding, ReplyCreate
from flowfield.results import Results
from flowfield.thread_view import ThreadView


def message(workspace, task="task-0", action="observation", body="Why this approach?"):
    gate = Conversation(workspace).eligibility("harbor", task)
    return ReplyCreate(
        binding=ReplyBinding.model_validate(
            gate.model_dump(include=set(ReplyBinding.model_fields))
        ),
        action=action,
        body=body,
    )


def queue(execution, enabled):
    execution.queue(
        "harbor",
        QueueEdit(expected_revision=execution.settings("harbor").revision, enabled=enabled),
    )


def test_bound_answer_is_one_atomic_receipt_and_continues_once(tmp_path):
    execution = fixture(tmp_path)
    run, question_record = question(execution)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=BASE)
    replies = Replies(execution.workspace)
    request = message(execution.workspace, action="answer", body="Keep offline support")
    answer = replies.submit("harbor", "task-0", request)
    assert answer.status == "recorded"
    assert replies.submit("harbor", "task-0", request) == answer
    gate = Conversation(execution.workspace).eligibility("harbor", "task-0")
    assert gate.enabled and gate.reason == "answer_editable"
    revision = message(
        execution.workspace, action="answer", body="Keep offline support for all data"
    )
    replies.submit("harbor", "task-0", revision)
    late_edit = message(execution.workspace, action="answer", body="A correction after consumption")
    continuation = execution.claim("harbor", BASE, {BASE: set()})
    assert continuation.input_question_id == question_record.id
    assert (
        "Keep offline support for all data"
        in execution.assignment("harbor", continuation.id)["input"]
    )
    with pytest.raises(ApplicationError, match="processing"):
        replies.submit("harbor", "task-0", late_edit)
    assert execution.claim("harbor", BASE, {}) is None
    thread = ThreadView(execution.workspace).page_view("harbor", "task-0")
    assert len([m for m in thread.items if m.kind == "answer"]) == 2
    assert not any(m.kind == "reply" for m in thread.items)
    latest = ThreadView(execution.workspace).item_view(
        "harbor", "task-0", f"question:{question_record.id}"
    )
    assert latest.kind == "answer" and latest.body == "Keep offline support for all data"


def test_human_test_feedback_reaches_same_task_without_approving_partial_work(tmp_path):
    execution = fixture(tmp_path)
    result(execution, partial=True)
    replies = Replies(execution.workspace)
    text = "I tried the selected version: desktop keyboard play works. Finish the remaining checks."
    request = message(execution.workspace, action="changes", body=text)
    receipt = replies.submit("harbor", "task-0", request)
    assert replies.submit("harbor", "task-0", request) == receipt
    version = Results(execution.workspace).page("harbor", "task-0").items[0]
    assert version.status == "changes_requested" and version.approved_at is None
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run.purpose == "work" and run.task_id == "task-0"
    assignment = execution.assignment("harbor", run.id)
    assert assignment["feedback"] == text and "Add a check" in assignment["predecessor"]
    assert execution.workspace.task("harbor", "task-0").status != "done"


@pytest.mark.parametrize("parallel", [False, True])
def test_human_testing_records_exact_result_without_work_and_reaches_successor(tmp_path, parallel):
    service, repo, work = result_fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    if parallel:
        settings = service.execution.settings("harbor")
        service.execution.configure(
            "harbor",
            SettingsEdit(
                expected_revision=settings.revision, max_parallel=2, selection=settings.selection
            ),
        )
        task = service.workspace.create_task(
            "harbor", task_request(title="Independent", status="up_next", body="Investigate")
        )
        service.workspace.publish_task(
            "harbor",
            task.id,
            TaskPublish(
                completion="report",
                expected_revision=task.revision,
            ),
        )
        queue(service.execution, True)
        independent = service.execution.claim("harbor", work.base_commit, {})
        service.execution.started("harbor", independent.id)
        queue(service.execution, False)
    replies = Replies(service.workspace)
    text = "I tried Result 1: keyboard and reload work; narrow view not tested."
    request = message(service.workspace, "work", action="observation", body=text)
    before = service.workspace.task("harbor", "work")
    receipt = replies.submit("harbor", "work", request)
    assert receipt.status == "recorded" and receipt.run_id is None
    assert replies.submit("harbor", "work", request) == receipt
    assert service.execution.claim("harbor", work.base_commit, {}) is None
    assert current(service) == version and service.workspace.task("harbor", "work") == before
    restarted = Workspace(service.workspace.directory)
    entry = ThreadView(restarted).item_view("harbor", "work", "reply:" + receipt.id)
    assert entry.title == "Human testing · Result 1" and entry.result_id == version.id
    assert entry.body == text
    source = Conversation(restarted).source("harbor", "work", "reply:" + receipt.id)
    assert json.loads(source.text)["binding"]["result_id"] == version.id
    replies.submit("harbor", "work", message(service.workspace, "work", action="changes"))
    queue(service.execution, True)
    successor = service.execution.claim("harbor", work.base_commit, {})
    frozen = json.loads(service.execution.assignment("harbor", successor.id)["human_testing"])
    assert frozen["items"][0]["body"] == text
    assert frozen["items"][0]["binding"]["result_id"] == version.id
    assert "not successor code" in frozen["authority"]
    if parallel:
        assert service.execution.get("harbor", independent.id).status == "running"
        assert (
            json.loads(service.execution.assignment("harbor", independent.id)["human_testing"])
            == []
        )


def test_testing_requires_idle_exact_result_and_does_not_reopen_done(tmp_path):
    execution = fixture(tmp_path / "no-result")
    with pytest.raises(ApplicationError, match="Select a result"):
        Replies(execution.workspace).submit(
            "harbor", "task-0", message(execution.workspace, action="observation")
        )
    service, repo, work = result_fixture(tmp_path / "result")
    service.results.process("harbor")
    version = current(service)
    request = message(service.workspace, "work", action="observation")
    approve(service, version)
    service.results.process("harbor")
    replies = Replies(service.workspace)
    with pytest.raises(ApplicationError, match="changed"):
        replies.submit("harbor", "work", request)
    done = current(service)
    receipt = replies.submit(
        "harbor", "work", message(service.workspace, "work", action="observation")
    )
    assert receipt.status == "recorded"
    assert service.workspace.task("harbor", "work").status == "done"
    assert current(service) == done


def test_thread_is_bounded_links_exact_revisions_and_keeps_long_sources(tmp_path):
    execution = fixture(tmp_path)
    thread = ThreadView(execution.workspace)
    for index in range(24):
        task = execution.workspace.task("harbor", "task-0")
        execution.workspace.edit_task(
            "harbor",
            task.id,
            TaskEdit(
                expected_revision=task.revision, body=f"Revision {index}\n" + "Detail " * 1200
            ),
        )
    page = thread.page_view("harbor", "task-0")
    assert len(page.items) == 20 and page.next_cursor
    newest = page.items[0]
    assert newest.truncated and len(newest.body) == 6000
    assert len(thread.item_view("harbor", "task-0", newest.id, full=True).body) > 6000
    assert thread.item_view("harbor", "task-0", "definition:1").title == "Task defined"
    older = thread.page_view("harbor", "task-0", page.next_cursor)
    assert not {m.id for m in older.items} & {m.id for m in page.items}


def test_registered_mcp_reply_and_browser_thread_share_durable_receipts(tmp_path):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from test_connection import running_service

    execution = fixture(tmp_path)
    result(execution, partial=True)
    queue(execution, False)
    request = message(execution.workspace, action="observation")

    async def exercise(base):
        async with (
            streamable_http_client(base + "/mcp/") as (read, write, _),
            ClientSession(read, write) as session,
            httpx.AsyncClient(base_url=base, trust_env=False) as api,
        ):
            await session.initialize()
            scope = {"project_id": "harbor", "task_id": "task-0"}
            gate = await session.call_tool("get_task_input", scope)
            assert gate.structuredContent["enabled"]
            args = {**scope, "request": request.model_dump()}
            first = await session.call_tool("reply_to_task", args)
            assert not first.isError
            assert (
                await session.call_tool("reply_to_task", args)
            ).structuredContent == first.structuredContent
            thread = (await api.get("/api/projects/harbor/tasks/task-0/thread")).json()
            assert thread["items"][0]["body"] == request.body
            assert first.structuredContent["status"] == "recorded"
            assert not execution.settings("harbor").enabled

    with running_service(execution.workspace.directory) as base:
        asyncio.run(exercise(base))


def test_new_worker_discussion_is_rejected_without_scheduling(tmp_path):
    execution = fixture(tmp_path)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReplyCreate.model_validate(
            {**message(execution.workspace).model_dump(), "action": "message"}
        )
    with execution.workspace.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM task_replies").fetchone()[0] == 0
    queue(execution, True)
    assert execution.claim("harbor", BASE, {}).purpose == "work"
