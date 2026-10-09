"""Durable answers, exact continuation ownership and preserved assignment evidence."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from project_fixtures import fixture_stage_change
from test_execution import BASE, RESULT, fixture

from flowfield.application import TaskEdit
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, RunAction
from flowfield.questions import QuestionAnswer, QuestionCreate, Questions


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


def test_answer_survives_restart_and_claims_once_with_frozen_evidence(tmp_path):
    execution = fixture(tmp_path)
    run, q = question(execution)
    questions = Questions(execution.workspace)
    saved = answer(questions, q)
    assert saved.delivery.state == "Saving work"
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    execution.restart()
    assert questions.get("harbor", q.id).delivery.state == "Paused"
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
    queue(execution, True)
    with ThreadPoolExecutor(2) as pool:
        attempts = list(
            pool.map(lambda _: execution.claim("harbor", BASE, {RESULT: set()}), range(2))
        )
    successor = next(r for r in attempts if r)
    assert sum(r is not None for r in attempts) == 1
    assert successor.predecessor_id == run.id
    assert successor.input_base_commit == RESULT and successor.base_commit == BASE
    original = execution.assignment("harbor", run.id)
    current = execution.assignment("harbor", successor.id)
    assert current["description"] == original["description"]
    assert current["agreement"] == original["agreement"]
    assert json.loads(current["input"])[0]["answer_revision"] == saved.revision
    assert json.loads(current["input"])[0]["answer"] == saved.answer
    consumed = questions.get("harbor", q.id)
    assert consumed.status == "assigned" and consumed.delivery.state == "Resuming"
    assert consumed.continuation_run_id == successor.id and not consumed.delivery.can_edit
    assert answer(questions, q).continuation_run_id == successor.id  # lost-response replay
    with pytest.raises(ApplicationError, match="already assigned"):
        answer(questions, saved, "Current page")
    execution.started("harbor", successor.id)
    assert questions.get("harbor", q.id).delivery.state == "Working"
    correction = questions.correct(
        "harbor",
        q.id,
        QuestionAnswer(
            expected_revision=consumed.revision,
            answer="Use the current page instead.",
        ),
    )
    assert correction.id != q.id and correction.origin_run_id is None
    assert questions.get("harbor", q.id).answer == saved.answer
    assert execution.workspace.task("harbor", run.task_id).blocking_questions[0].id == correction.id
    assert (
        questions.correct(
            "harbor",
            q.id,
            QuestionAnswer(
                expected_revision=consumed.revision,
                answer="Use the current page instead.",
            ),
        ).id
        == correction.id
    )


@pytest.mark.parametrize("gate", ["pause", "stop", "scope", "uncertain", "missing_checkpoint"])
def test_answers_do_not_override_execution_gates(tmp_path, gate):
    execution = fixture(tmp_path)
    run, q = question(execution)
    questions = Questions(execution.workspace)
    saved = answer(questions, q)
    execution.finish(
        "harbor",
        run.id,
        "uncertain" if gate == "uncertain" else "waiting_for_input",
        input_checkpoint=None if gate == "missing_checkpoint" else RESULT,
    )
    if gate == "pause":
        queue(execution, False)
    elif gate == "stop":
        current = execution.get("harbor", run.id)
        execution.stop_requested("harbor", run.id, RunAction(expected_revision=current.revision))
    elif gate == "scope":
        task = execution.workspace.task("harbor", run.task_id)
        execution.workspace.edit_task(
            "harbor",
            task.id,
            TaskEdit(expected_revision=task.revision, body="A materially different outcome"),
        )
    assert execution.claim("harbor", BASE, {RESULT: set()}) is None
    current = questions.get("harbor", q.id)
    assert current.answer == saved.answer and current.continuation_run_id is None
    assert current.delivery.state in (
        "Paused",
        "Continuation blocked",
        "Needs update",
        "Check execution",
    )


def test_edit_races_claim_at_the_answer_consumption_boundary(tmp_path):
    execution = fixture(tmp_path)
    run, q = question(execution)
    questions = Questions(execution.workspace)
    saved = answer(questions, q)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)

    def edit():
        try:
            return answer(questions, saved, "Current page")
        except ApplicationError as error:
            return error

    with ThreadPoolExecutor(2) as pool:
        editing = pool.submit(edit)
        claiming = pool.submit(execution.claim, "harbor", BASE, {RESULT: set()})
        edited, successor = editing.result(), claiming.result()
    snapshot = json.loads(execution.assignment("harbor", successor.id)["input"])[0]
    assert snapshot["answer"] == (
        "All filtered rows" if isinstance(edited, ApplicationError) else "Current page"
    )
    assert snapshot["answer_revision"] == questions.get("harbor", q.id).consumed_answer_revision


def test_stopped_pending_continuation_requires_explicit_retry(tmp_path):
    execution = fixture(tmp_path)
    run, q = question(execution)
    answer(Questions(execution.workspace), q)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    stopped = execution.stop_requested(
        "harbor", run.id, RunAction(expected_revision=execution.get("harbor", run.id).revision)
    )
    assert execution.claim("harbor", BASE, {RESULT: set()}) is None
    execution.retry("harbor", run.id, RunAction(expected_revision=stopped.revision))
    successor = execution.claim("harbor", BASE, {RESULT: set()})
    assert successor.input_base_commit == RESULT


def test_capacity_and_coordinator_questions_remain_real_gates(tmp_path):
    execution = fixture(tmp_path, count=2)
    run, q = question(execution)
    answer(Questions(execution.workspace), q)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    queue(execution, False)
    # Another blocking input must prevent continuation even when the managed answer exists.
    Questions(execution.workspace).ask(
        "harbor",
        QuestionCreate(
            task_id=run.task_id,
            question="Changed audience?",
            context="Consequential change",
            recommendation="Discuss scope",
            blocking_scope="Audience",
        ),
    )
    queue(execution, True)
    occupied = execution.claim("harbor", BASE, {BASE: set(), RESULT: set()})
    assert occupied.task_id != run.task_id
    assert execution.claim("harbor", BASE, {BASE: set(), RESULT: set()}) is None
    assert Questions(execution.workspace).get("harbor", q.id).continuation_run_id is None


def test_supervisor_preserves_pre_question_code_and_report_baseline(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    from test_supervisor import FakeWorker

    from flowfield.adapters.git_workspace import baseline, git
    from flowfield.supervisor import Supervisor

    class AskingWorker(FakeWorker):
        async def run(self, model, effort, prompt, tools):
            brief = json.loads(prompt)
            if "input" not in brief["sections"]:
                (self.cwd / "unfinished.txt").write_text("Preserve this work before the answer")
                await self.on_tool(
                    "ask_question",
                    {
                        "question": "Which rows?",
                        "context": "Product scope missing",
                        "recommendation": "All filtered rows",
                    },
                )
            else:
                assert (
                    self.cwd / "unfinished.txt"
                ).read_text() == "Preserve this work before the answer"
                inputs = json.loads(
                    json.loads(await self.on_tool("read_context", {"section": "input"}))["text"]
                )
                assert inputs[-1]["answer"] == "All filtered rows"
                await self.on_tool(
                    "submit_result",
                    {
                        "outcome": "complete",
                        "summary": "Report",
                        "checks": "Read preserved code and input",
                    },
                )
            return {"status": "completed"}

    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", AskingWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture")
    execution = fixture(tmp_path)
    repo = Path(tmp_path / "harbor")
    git(repo, "init")
    (repo / "base.txt").write_text("baseline")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    base = baseline(repo)
    service = Supervisor(execution.workspace)
    from flowfield.integration_models import IntegrationConfig

    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local", expected_revision=1, target_branch="integration", checks=["true"]
        ),
    )

    async def exercise():
        run = execution.claim("harbor", base, {base: set()})
        await service._execute(run, repo)
        waiting = execution.get("harbor", run.id)
        assert waiting.status == "waiting_for_input", waiting.problem
        assert waiting.input_checkpoint and waiting.result_commit is None
        q = Questions(execution.workspace).get("harbor", waiting.question_id)
        answer(Questions(execution.workspace), q)
        successor = execution.claim("harbor", base, {waiting.input_checkpoint: set()})
        await service._execute(successor, repo)
        completed = execution.get("harbor", successor.id)
        assert completed.status == "in_review", completed.problem
        assert (
            git(repo, "show", completed.result_commit + ":unfinished.txt")
            .decode()
            .startswith("Preserve")
        )
        assert baseline(repo) == base
        # A report cannot hide source changes made before its question by rebasing its baseline.
        service.results.process("harbor")
        result = service.results.page("harbor", run.task_id).items[0]
        assert result.status == "blocked"
        assert execution.workspace.task("harbor", run.task_id).status != "done"

    asyncio.run(exercise())


def test_answer_waits_for_capacity_and_uncertain_assigned_work_is_not_replayed(tmp_path):
    execution = fixture(tmp_path, count=2)
    run, q = question(execution)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    occupied = execution.claim("harbor", BASE, {BASE: set()})
    assert occupied.task_id != run.task_id
    questions = Questions(execution.workspace)
    assert answer(questions, q).delivery.state == "Waiting for capacity"
    assert execution.claim("harbor", BASE, {RESULT: set()}) is None
    execution.finish("harbor", occupied.id, "stopped")
    successor = execution.claim("harbor", BASE, {RESULT: set()})
    execution.restart()
    assert questions.get("harbor", q.id).delivery.state == "Check execution"
    queue(execution, True)
    assert execution.claim("harbor", BASE, {RESULT: set()}) is None
    assert execution.get("harbor", successor.id).status == "uncertain"


def test_failed_input_continuation_retry_keeps_answer_and_checkpoint(tmp_path):
    execution = fixture(tmp_path)
    run, q = question(execution)
    questions = Questions(execution.workspace)
    answer(questions, q)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    successor = execution.claim("harbor", BASE, {RESULT: set()})
    failed = execution.finish("harbor", successor.id, "failed", problem="Setup failed")
    assert questions.get("harbor", q.id).delivery.state == "Continuation blocked"
    execution.retry("harbor", successor.id, RunAction(expected_revision=failed.revision))
    retry = execution.claim("harbor", BASE, {RESULT: set()})
    assert retry.base_commit == BASE and retry.input_base_commit == RESULT
    assert (
        execution.assignment("harbor", retry.id)["input"]
        == execution.assignment("harbor", successor.id)["input"]
    )
    current = questions.get("harbor", q.id)
    assert current.delivery.state == "Resuming" and current.delivery.run_id == retry.id
    assert current.continuation_run_id == successor.id  # Original reservation evidence stays fixed.


def test_explicit_retry_after_scope_revision_cannot_relabel_old_answer_as_new_assignment(tmp_path):
    from flowfield.application import TaskPublish

    execution = fixture(tmp_path)
    run, q = question(execution)
    answer(Questions(execution.workspace), q)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=RESULT)
    successor = execution.claim("harbor", BASE, {RESULT: set()})
    failed = execution.finish("harbor", successor.id, "failed", problem="Setup failed")
    task = execution.workspace.task("harbor", run.task_id)
    execution.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, body="A new agreed report outcome"),
    )
    execution.retry("harbor", failed.id, RunAction(expected_revision=failed.revision))
    task = execution.workspace.task("harbor", run.task_id)
    execution.workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            stages=fixture_stage_change(execution.workspace, "harbor", task.id),
            expected_revision=task.revision,
            completion="report",
        ),
    )
    retry = execution.claim("harbor", BASE, {BASE: set()})
    assert retry.input_base_commit is None and retry.input_question_id is None
    assignment = execution.assignment("harbor", retry.id)
    assert assignment["description"] == "A new agreed report outcome"
    assert "input" not in assignment
