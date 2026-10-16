"""Canonical chronology, shared stage authority and deliberate frozen worker context."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from execution_fixtures import BASE, fixture
from test_input_continuation import answer, question

from flowfield.activity import ActivityCreate
from flowfield.application import TaskEdit, TaskPriority, Workspace
from flowfield.conversation import Conversation
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerResult
from flowfield.questions import Questions
from flowfield.stage_models import Stage, StageUpdate
from flowfield.stages import Stages
from flowfield.supervisor import WorkerBridge
from flowfield.worker_context import brief_context, continue_input


def plan(revision=0, agreement=1, status="planned"):
    return StageUpdate(
        expected_revision=revision,
        agreement_revision=agreement,
        stages=[
            Stage(
                id="implement",
                title="Implement",
                outcome="Deliver the requested behavior",
                status=status,
            ),
            Stage(id="verify", title="Verify", outcome="Demonstrate the agreed outcome"),
        ],
        reason="Initial sequence" if revision == 0 else "Implementation started",
    )


def test_stages_have_one_owner_and_cannot_complete_or_remove_scope(tmp_path):
    execution = fixture(tmp_path, stages=plan().stages)
    stages = Stages(execution.workspace)
    initial = stages.get("harbor", "task-0")
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert (
        json.loads(execution.assignment("harbor", run.id)["stages"])["revision"] == initial.revision
    )
    execution.started("harbor", run.id)
    with pytest.raises(ApplicationError, match="worker owns"):
        stages.update("harbor", "task-0", plan(1))
    updated = stages.update("harbor", "task-0", plan(1, status="active"), run_id=run.id)
    assert updated.run_id == run.id and updated.author == "worker"
    assert stages.update("harbor", "task-0", plan(1, status="active"), run_id=run.id) == updated
    with pytest.raises(ApplicationError, match="Keep existing stage outcomes"):
        stages.update(
            "harbor",
            "task-0",
            StageUpdate(
                expected_revision=2,
                agreement_revision=1,
                stages=[updated.stages[0]],
                reason="Delete verification",
            ),
            run_id=run.id,
        )
    with pytest.raises(ApplicationError):
        stages.update("harbor", "task-0", plan(2), run_id="another-worker")
    reworded = plan(2, status="completed")
    reworded.stages[0].outcome = "Delivered the requested behavior"
    with pytest.raises(ApplicationError, match="Include stage 'implement'") as rejected:
        stages.update("harbor", "task-0", reworded, run_id=run.id)
    assert "Deliver the requested behavior" in str(rejected.value)
    assert "put progress evidence in reason" in str(rejected.value)
    assert stages.get("harbor", "task-0") == updated
    complete = plan(2).model_copy(
        update={"stages": [s.model_copy(update={"status": "completed"}) for s in updated.stages]}
    )
    stages.update("harbor", "task-0", complete, run_id=run.id)
    assert execution.workspace.task("harbor", "task-0").status == "in_progress"
    execution.finish("harbor", run.id, "failed", problem="fixture")
    with pytest.raises(ApplicationError):
        stages.update("harbor", "task-0", plan(3), run_id=run.id)
    restored = Stages(Workspace(execution.workspace.directory)).get("harbor", "task-0")
    assert restored.revision == 3
    assert Conversation(execution.workspace).source("harbor", "task-0", "plan:1").item.revision == 1


def test_plan_writes_and_claims_serialize_and_scope_changes_do_not_rebind(tmp_path):
    execution = fixture(tmp_path, stages=plan().stages)
    stages = Stages(execution.workspace)
    with ThreadPoolExecutor(2) as pool:

        def update(_):
            try:
                return stages.update("harbor", "task-0", plan(1, status="active"))
            except ApplicationError:
                return None

        results = list(pool.map(update, range(2)))
    assert sum(value is not None for value in results) == 1
    task = execution.workspace.task("harbor", "task-0")
    execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, body="Changed scope")
    )
    with pytest.raises(ApplicationError):
        stages.update("harbor", task.id, plan(1))
    assert stages.get("harbor", task.id).agreement_revision == 1


@pytest.mark.parametrize("outcome", ["complete", "partial"])
def test_submission_reconciles_progress_without_closing_rejected_worker(tmp_path, outcome):
    execution = fixture(tmp_path, stages=plan().stages)
    stages = Stages(execution.workspace)
    initial = stages.get("harbor", "task-0")
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    bridge = WorkerBridge(execution, run, SimpleNamespace())
    submission = {"outcome": outcome, "summary": "Findings", "checks": "Observed checks"}
    if outcome == "partial":
        submission["remaining_work"] = "Implementation remains unfinished"
    else:
        with pytest.raises(ApplicationError, match="implement, verify"):
            asyncio.run(bridge.call("submit_result", submission))
        assert bridge.result is None
        assert execution.get("harbor", run.id).result is None
        assert stages.get("harbor", "task-0") == initial
        completed = plan(1).model_copy(
            update={
                "stages": [s.model_copy(update={"status": "completed"}) for s in initial.stages]
            }
        )
        asyncio.run(bridge.call("update_stages", completed.model_dump()))
    asyncio.run(bridge.call("submit_result", submission))
    assert bridge.result.outcome == outcome
    assert execution.workspace.task("harbor", "task-0").status == "in_progress"


def test_conversation_paging_sources_questions_and_no_reorder_noise(tmp_path):
    execution = fixture(tmp_path, count=2)
    workspace = execution.workspace
    thread = Conversation(workspace)
    initial = thread.page("harbor", "task-0")
    task = workspace.task("harbor", "task-0")
    workspace.prioritize_task(
        "harbor",
        task.id,
        TaskPriority(expected_revision=task.revision, status="up_next", before_id="task-1"),
    )
    assert thread.page("harbor", task.id).items == initial.items
    for i in range(35):
        workspace.add_activity(
            "harbor", ActivityCreate(task_id=task.id, body=f"Observation {i}: " + "界" * 20000)
        )
    first = thread.page("harbor", task.id, limit=10)
    assert len(first.model_dump_json().encode()) < 23000
    workspace.add_activity("harbor", ActivityCreate(task_id=task.id, body="Arrived while reading"))
    seen = [item.id for item in first.items]
    cursor = first.next_cursor
    while cursor:
        page = thread.page("harbor", task.id, cursor, limit=10)
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
    assert len(seen) == len(set(seen)) == 35 + len(initial.items)
    source = thread.source("harbor", task.id, first.items[0].id)
    text = source.text
    while source.next_offset:
        source = thread.source("harbor", task.id, source.item.id, source.next_offset)
        text += source.text
    assert "界" * 20000 in json.loads(text)["body"]
    with pytest.raises(ApplicationError, match="not found"):
        thread.source("harbor", "task-1", first.items[0].id)
    _, q = question(execution)
    saved = answer(Questions(workspace), q)
    items = thread.page("harbor", task.id).items
    assert {i.kind for i in items} >= {"question", "answer", "attempt"}
    assert (
        json.loads(thread.source("harbor", task.id, f"question:{q.id}:{saved.revision}").text)[
            "answer"
        ]
        == saved.answer
    )


def test_eligibility_tracks_ownership_and_exact_input_binding(tmp_path):
    execution = fixture(tmp_path)
    thread = Conversation(execution.workspace)
    assert thread.eligibility("harbor", "task-0").enabled
    run, q = question(execution)
    # Model has asked, but command cleanup/capture still owns the work.
    pending = thread.eligibility("harbor", "task-0")
    assert not pending.enabled and pending.run_id == run.id and pending.question_id == q.id
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=BASE)
    # Run/question records are sufficient; routine status must not become a note.
    with execution.workspace.connection() as db:
        assert not db.execute(
            "SELECT 1 FROM activity WHERE task_id=? AND kind='note'", (run.task_id,)
        ).fetchone()
    expected = thread.eligibility("harbor", "task-0")
    assert expected.enabled and expected.reason == "answer_expected"
    assert expected.question_revision == q.revision
    answer(Questions(execution.workspace), q)
    continuation = execution.claim("harbor", BASE, {BASE: set()})
    assert not thread.eligibility("harbor", "task-0").enabled
    assert continuation.id != run.id


def test_long_exchanges_preserve_answers_without_replaying_question_context():
    sections = {"description": "界" * 20000, "feedback": "latest feedback"}
    for i in range(80):
        continue_input(
            sections,
            {
                "question_id": str(i),
                "answer_revision": 2,
                "question": "Clarify",
                "context": "Older verbose context " * 1000,
                "answer": f"Constraint {i}: keep offline support",
            },
        )
    assert len(json.loads(sections["input"])) == 1
    assert len(json.loads(sections["earlier_answers"])) == 79
    assert "Constraint 0" in sections["earlier_answers"]
    assert "Older verbose" not in sections["earlier_answers"]
    assert len(json.loads(sections["exchange_history"])) == 79
    brief = brief_context(sections)
    assert brief["truncated_sections"] == ["description"]
    assert len(json.dumps(brief, ensure_ascii=False).encode()) < 23000


def test_brief_includes_complete_small_essentials_and_pages_large_constraints():
    sections = {
        "description": "Current agreement",
        "feedback": "Current feedback",
        "input": '{"answer":"Keep offline use"}',
        "milestone": "Constraint " * 1500,
        "stages": '{"revision":3}',
        "questions": "[]",
        "project": "",
        "human_testing": '{"items":[]}',
    }
    before = dict(sections)
    brief = brief_context(sections)
    assert brief["context"]["input"] == sections["input"]
    assert brief["context"]["stages"] == sections["stages"]
    assert "milestone" not in brief["context"] and "questions" not in brief["context"]
    assert brief["sections"]["milestone"] == len(sections["milestone"])
    assert len(json.dumps(brief["context"])) <= 10000
    assert sections == before  # Full omitted constraints remain in the frozen source.


def test_worker_reads_only_frozen_report_sources_and_stage_updates_are_scoped(tmp_path):
    execution = fixture(tmp_path, stages=plan().stages)
    old = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", old.id)
    execution.finish(
        "harbor",
        old.id,
        "failed",
        result=WorkerResult(
            summary="Evidence " * 300,
            checks="checked",
            limitations="unresolved: preserve offline use",
        ),
        problem="fixture",
    )
    from flowfield.execution_models import RunAction

    failed = execution.get("harbor", old.id)
    execution.retry("harbor", old.id, RunAction(expected_revision=failed.revision))
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    bridge = WorkerBridge(execution, run, SimpleNamespace())
    report = json.loads(asyncio.run(bridge.call("read_context", {"section": f"attempt:{old.id}"})))
    assert "unresolved" in report["text"]
    assert "Evidence" not in execution.assignment("harbor", run.id)["attempt_sources"]
    assert (
        "unresolved: preserve offline use"
        in execution.assignment("harbor", run.id)["prior_concerns"]
    )
    with pytest.raises(ApplicationError, match="not in the frozen context"):
        asyncio.run(bridge.call("read_context", {"section": f"attempt:{run.id}"}))
    updated = json.loads(
        asyncio.run(bridge.call("update_stages", plan(1, status="active").model_dump()))
    )
    assert updated["run_id"] == run.id
    with execution.workspace.connection(write=True) as db:
        db.execute(
            "UPDATE runs SET data=json_set(data,'$.result.summary','Changed later') WHERE id=?",
            (old.id,),
        )
    with pytest.raises(ApplicationError, match="not in the frozen context"):
        asyncio.run(bridge.call("read_context", {"section": f"attempt:{old.id}"}))


def test_approval_and_delivery_have_distinct_linked_items(tmp_path):
    from result_fixtures import approve, current
    from result_fixtures import fixture as result_fixture

    service, _, _ = result_fixture(tmp_path)
    thread = Conversation(service.workspace)
    service.results.process("harbor")
    ready = current(service)
    before = thread.eligibility("harbor", "work")
    assert before.enabled and before.result_id == ready.id
    approve(service, ready)
    pending = thread.eligibility("harbor", "work")
    assert not pending.enabled and pending.reason == "processing_result"
    original = thread.source("harbor", "work", "result:" + ready.id)
    service.results.process("harbor")
    with pytest.raises(ApplicationError, match="Reread this source"):
        thread.source("harbor", "work", original.item.id, 1, original.item.revision)
    items = thread.page("harbor", "work").items
    assert {i.kind for i in items} >= {"result", "approval", "delivery"}
    assert thread.source("harbor", "work", "approval:" + ready.id).item.source_id == ready.id


def test_result_feedback_keeps_consumed_answers_within_the_same_agreement(tmp_path):
    from execution_fixtures import validate_report

    from flowfield.questions import QuestionCreate
    from flowfield.result_models import ResultReview
    from flowfield.results import Results
    from flowfield.work_state import task_state

    def state():
        with execution.workspace.connection() as db:
            return task_state(execution.workspace, db, "harbor", "task-0")

    execution = fixture(tmp_path)
    first, q = question(execution)
    answer(Questions(execution.workspace), q, "Keep offline use")
    execution.finish("harbor", first.id, "waiting_for_input", input_checkpoint=BASE)
    second = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", second.id)
    execution.finish(
        "harbor",
        second.id,
        "in_review",
        commit=BASE,
        result=WorkerResult(
            summary="Part delivered",
            checks="Checked first part",
            outcome="partial",
            remaining_work="Finish verification",
        ),
    )
    results = Results(execution.workspace)
    validate_report(execution)
    partial = results.page("harbor", "task-0").items[0]
    assert state().label == "Continue work"
    results.review(
        "harbor",
        partial.id,
        ResultReview(
            expected_revision=partial.revision,
            action="request_changes",
            candidate_commit=partial.candidate_commit,
            note="Finish verification within scope",
        ),
    )
    third = execution.claim("harbor", BASE, {BASE: set()})
    assert "Keep offline use" in execution.assignment("harbor", third.id)["input"]
    execution.started("harbor", third.id)
    newer = execution.ask_question(
        "harbor",
        third.id,
        QuestionCreate(
            task_id="task-0",
            question="Which output format?",
            context="Two formats are possible.",
            recommendation="Plain text",
        ),
    )
    execution.finish("harbor", third.id, "waiting_for_input", input_checkpoint=BASE)
    assert state().label == "Needs your answer"
    assert newer.id in state().href
    answer(Questions(execution.workspace), newer, "Plain text")
    fourth = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", fourth.id)
    execution.finish(
        "harbor",
        fourth.id,
        "in_review",
        commit=BASE,
        result=WorkerResult(summary="Findings delivered", checks="Checked", outcome="complete"),
    )
    validate_report(execution)
    assert state().label == "Completed" and state().tone == "complete"
    assert Questions(execution.workspace).get("harbor", q.id).status == "assigned"


def test_mcp_conversation_cursor_round_trips_without_sdk_coercion(tmp_path):
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from service_fixtures import running_service

    execution = fixture(tmp_path)
    workspace = execution.workspace
    for index in range(3):
        workspace.add_activity("harbor", ActivityCreate(task_id="task-0", body=f"Note {index}"))
    expected = Conversation(workspace).page("harbor", "task-0").items

    async def exercise(base):
        async with (
            streamable_http_client(base + "/mcp/") as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tool = next(
                t for t in (await session.list_tools()).tools if t.name == "get_task_conversation"
            )
            limit = tool.inputSchema["properties"]["limit"]
            assert limit["minimum"] == 1 and limit["maximum"] == 30
            args = {"project_id": "harbor", "task_id": "task-0", "limit": 1, "cursor": None}
            seen = []
            while True:
                result = await session.call_tool("get_task_conversation", args)
                assert not result.isError, result
                page = result.structuredContent
                seen.extend(item["id"] for item in page["items"])
                if not page["next_cursor"]:
                    break
                args["cursor"] = page["next_cursor"]
            assert seen == [item.id for item in expected]
            for invalid in ("[]", "{}", '["one"]', "[1, 2]"):
                assert (
                    await session.call_tool("get_task_conversation", {**args, "cursor": invalid})
                ).isError

    with running_service(workspace.directory) as base:
        anyio.run(exercise, base)
