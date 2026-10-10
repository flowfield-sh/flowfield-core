"""Combined capacity, continuation and changed-intent races without model calls."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from project_fixtures import task_request
from test_conversation import plan
from test_execution import BASE, RESULT, fixture, validate_report
from test_input_continuation import answer

from flowfield.application import TaskEdit, TaskPublish
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction, Usage, WorkerResult
from flowfield.questions import QuestionCreate, Questions
from flowfield.reads import ContextReads
from flowfield.results import Results
from flowfield.stages import Stages


def test_changed_stage_agreement_blocks_only_its_task_until_coordinator_reconciles(tmp_path):
    execution = fixture(tmp_path, count=2, cap=2, stages=plan().stages)
    workspace = execution.workspace
    stages = Stages(workspace)
    original = stages.get("harbor", "task-0")
    task = workspace.task("harbor", "task-0")
    task = workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, body="Refined scope")
    )
    publication = TaskPublish(
        completion="report",
        expected_revision=task.revision,
    )
    with pytest.raises(ApplicationError, match="current stages"):
        workspace.publish_task("harbor", task.id, publication)
    task = workspace.task("harbor", task.id)
    card = BrowserReads(workspace).task("harbor", task.id)
    brief = ContextReads(workspace).task("harbor", task.id)
    assert task.readiness == card.readiness == brief["readiness"] == "needs_reconciliation"
    assert task.preparation_issue == card.preparation_issue == brief["preparation_issue"]
    assert "update its stages" in task.preparation_issue
    assert card.state.label == "Reconcile stages"
    board = BrowserReads(workspace).board("harbor")
    assert next(item for item in board.tasks if item.id == task.id).state == card.state
    independent = execution.claim("harbor", BASE, {BASE: set()})
    assert independent.task_id == "task-1"
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
    assert stages.get("harbor", task.id) == original
    stages.update("harbor", task.id, plan(1, agreement=task.agreement_revision))
    workspace.publish_task("harbor", task.id, publication)
    assert workspace.task("harbor", task.id).readiness == "ready"
    resumed = execution.claim("harbor", BASE, {BASE: set()})
    assert resumed.task_id == task.id
    frozen = json.loads(execution.assignment("harbor", resumed.id)["stages"])
    assert frozen["agreement_revision"] == task.agreement_revision
    assert frozen["stages"] == [stage.model_dump() for stage in original.stages]
    assert execution.get("harbor", independent.id).status == "preparing"


@pytest.mark.parametrize("restart", [False, True])
def test_answer_continues_once_beside_independent_work(tmp_path, restart):
    execution = fixture(tmp_path, count=2, cap=2)
    asking = execution.claim("harbor", BASE, {BASE: set()})
    independent = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", asking.id)
    execution.started("harbor", independent.id)
    questions = Questions(execution.workspace)
    q = execution.ask_question(
        "harbor",
        asking.id,
        QuestionCreate(
            task_id=asking.task_id,
            question="Which rows?",
            context="Scope",
            recommendation="All rows",
            blocking_scope="Rows",
        ),
    )
    answer(questions, q, "All rows")
    execution.finish("harbor", asking.id, "waiting_for_input", input_checkpoint=RESULT)
    if restart:
        execution.restart()
        assert execution.get("harbor", independent.id).status == "uncertain"
        assert execution.settings("harbor").enabled
    with ThreadPoolExecutor(4) as pool:
        attempts = list(
            pool.map(
                lambda _: execution.claim("harbor", BASE, {RESULT: set(), BASE: set()}), range(4)
            )
        )
    continued = [run for run in attempts if run]
    assert len(continued) == 1
    successor = continued[0]
    assert successor.task_id == asking.task_id and successor.predecessor_id == asking.id
    assert successor.input_base_commit == RESULT
    assert (
        json.loads(execution.assignment("harbor", successor.id)["input"])[0]["answer"] == "All rows"
    )
    assert questions.get("harbor", q.id).continuation_run_id == successor.id
    assert execution.get("harbor", independent.id).status == ("uncertain" if restart else "running")
    task = execution.workspace.create_task(
        "harbor", task_request(id="task-2", title="Later", status="up_next", body="Report")
    )
    execution.workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            completion="report",
            expected_revision=task.revision,
        ),
    )
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
    with pytest.raises(ApplicationError, match="late result"):
        execution.finish(
            "harbor",
            asking.id,
            "in_review",
            result=WorkerResult(summary="Late", checks="None"),
            commit=BASE,
        )
    # Confirming the independent stop frees only that slot; the successor stays owned.
    current = execution.get("harbor", independent.id)
    execution.stop_requested(
        "harbor", independent.id, RunAction(expected_revision=current.revision)
    )
    execution.finish("harbor", independent.id, "stopped")
    third = execution.claim("harbor", BASE, {BASE: set()})
    assert third.task_id == "task-2"
    assert execution.get("harbor", successor.id).status == "preparing"


def test_changed_intent_only_blocks_its_own_parallel_result(tmp_path):
    execution = fixture(tmp_path, count=2, cap=2)
    changed = execution.claim("harbor", BASE, {BASE: set()})
    independent = execution.claim("harbor", BASE, {BASE: set()})
    for run in (changed, independent):
        execution.started("harbor", run.id)
    task = execution.workspace.task("harbor", changed.task_id)
    execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, body="Changed outcome")
    )
    for run in (changed, independent):
        execution.finish(
            "harbor",
            run.id,
            "in_review",
            result=WorkerResult(summary="Report", checks="Read source", outcome="complete"),
            commit=RESULT,
        )
        validate_report(execution)
    assert (
        Results(execution.workspace).page("harbor", changed.task_id).items[0].problem_code
        == "assignment_changed"
    )
    assert execution.workspace.task("harbor", changed.task_id).status == "in_review"
    assert execution.workspace.task("harbor", independent.task_id).status == "done"
    assert execution.assignment("harbor", changed.id)["description"] == task.body


def test_stop_remains_bound_to_attempt_across_progress_and_retry(tmp_path):
    execution = fixture(tmp_path, count=2, cap=2)
    selected = execution.claim("harbor", BASE, {BASE: set()})
    independent = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", selected.id)
    execution.started("harbor", independent.id)
    confirmation = RunAction(expected_revision=execution.get("harbor", selected.id).revision)
    execution.usage("harbor", selected.id, Usage(total_tokens=100))
    current = execution.get("harbor", selected.id)
    assert current.revision > confirmation.expected_revision
    with pytest.raises(ApplicationError, match="stale"):
        execution.stop_requested(
            "harbor", selected.id, RunAction(expected_revision=current.revision + 1)
        )
    stopped = execution.stop_requested("harbor", selected.id, confirmation)
    assert stopped.status == "stopping"
    assert execution.stop_requested("harbor", selected.id, confirmation) == stopped
    stopped = execution.finish("harbor", selected.id, "stopped")
    assert execution.get("harbor", independent.id).status == "running"
    execution.retry("harbor", selected.id, RunAction(expected_revision=stopped.revision))
    successor = execution.claim("harbor", BASE, {BASE: set()})
    assert successor.predecessor_id == selected.id
    assert execution.stop_requested("harbor", selected.id, confirmation).status == "stopped"
    assert execution.get("harbor", successor.id).status == "preparing"
    with pytest.raises(ApplicationError, match="late result"):
        execution.finish(
            "harbor",
            selected.id,
            "in_review",
            result=WorkerResult(summary="Late", checks="None"),
            commit=RESULT,
        )


def test_stop_does_not_reopen_completed_work(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary="Done", checks="None"),
        commit=RESULT,
    )
    with pytest.raises(ApplicationError, match="no longer running"):
        execution.stop_requested("harbor", run.id, RunAction(expected_revision=run.revision))
