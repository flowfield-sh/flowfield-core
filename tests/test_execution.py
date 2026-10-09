"""Race, review and ownership invariants. No models, credentials or live harness."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from project_fixtures import adopt, fixture_stage_change, task_request

from flowfield.agent_models import AgentChoice
from flowfield.application import (
    ProjectSetup,
    TaskEdit,
    TaskProgress,
    TaskPublish,
    Workspace,
)
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import (
    QueueEdit,
    RunAction,
    SettingsEdit,
    Usage,
    WorkerResult,
)
from flowfield.result_models import ResultReview
from flowfield.results import Results

BASE, RESULT = "a" * 40, "b" * 40


def fixture(tmp_path: Path, *, count: int = 1, cap: int = 1, stages=None) -> Execution:
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "harbor")))
    for index in range(count):
        task = workspace.create_task(
            "harbor",
            task_request(
                **({"stages": stages} if stages is not None else {}),
                id=f"task-{index}",
                title=f"Task {index}",
                body="Implement and test the assigned behavior.",
                status="up_next",
            ),
        )
        workspace.publish_task(
            "harbor",
            task.id,
            TaskPublish(
                stages=fixture_stage_change(workspace, "harbor", task.id),
                completion="report",
                expected_revision=task.revision,
            ),
        )
    execution = Execution(workspace)
    configured = execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=1,
            max_parallel=cap,
            selection=AgentChoice(model="test-model", effort="low"),
        ),
    )
    execution.queue("harbor", QueueEdit(expected_revision=configured.revision, enabled=True))
    return execution


def result(execution: Execution, *, process=True, partial=False):
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)
    finished = execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(
            summary="Implemented",
            checks="unittest passed",
            outcome="partial" if partial else "complete",
            remaining_work="Add a check" if partial else "",
        ),
        commit=RESULT,
    )
    if process:
        validate_report(execution)
    return finished


def validate_report(execution):
    # Transaction/ownership tests use synthetic commits; real tree checks are in test_results.
    with patch("flowfield.results.git", return_value=b"same-tree"):
        Results(execution.workspace).process("harbor")


def test_no_implicit_model_or_queue_and_stale_settings(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "harbor")))
    execution = Execution(workspace)
    settings = execution.settings("harbor")
    assert settings.selection is None and settings.max_parallel == 1
    with pytest.raises(ApplicationError, match="Choose a worker harness"):
        execution.queue("harbor", QueueEdit(expected_revision=1, enabled=True))
    execution.configure(
        "harbor",
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="explicit", effort="low")),
    )
    assert not execution.settings("harbor").enabled
    with pytest.raises(ApplicationError):
        execution.configure(
            "harbor",
            SettingsEdit(expected_revision=1, selection=AgentChoice(model="other", effort="high")),
        )


def test_worker_occupancy_distinguishes_uncertain_and_review(tmp_path: Path) -> None:
    execution = fixture(tmp_path, count=3, cap=3)
    assert execution.occupancy("harbor").model_dump() == {"active": 0, "uncertain": 0}
    review = result(execution)
    assert review.status == "in_review"
    running = execution.claim("harbor", BASE, {BASE: set()})
    uncertain = execution.claim("harbor", BASE, {BASE: set()})
    assert running and uncertain
    execution.started("harbor", running.id)
    execution.finish("harbor", uncertain.id, "uncertain", problem="Lost command observation")
    assert execution.occupancy("harbor").model_dump() == {"active": 1, "uncertain": 1}
    execution.finish("harbor", running.id, "stopped")
    assert execution.occupancy("harbor").model_dump() == {"active": 0, "uncertain": 1}


def test_competing_claims_capacity_and_pause(tmp_path: Path) -> None:
    execution = fixture(tmp_path, count=3, cap=2)
    with ThreadPoolExecutor(max_workers=5) as pool:
        claims = list(pool.map(lambda _: execution.claim("harbor", BASE, {BASE: set()}), range(5)))
    runs = [run for run in claims if run]
    assert len(runs) == 2 and len({r.task_id for r in runs}) == 2
    assert len({r.environment_id for r in runs}) == 2
    settings = execution.settings("harbor")
    execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
    assert all(run.status == "preparing" for run in execution.active())


def test_claim_preserves_exact_assignment_and_refuses_changed_launch(tmp_path: Path) -> None:
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    task = execution.workspace.task("harbor", run.task_id)
    execution.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, body="A materially different requirement"),
    )
    assert (
        execution.assignment("harbor", run.id)["description"]
        == "Implement and test the assigned behavior."
    )
    with pytest.raises(ApplicationError, match="older or blocked assignment"):
        execution.started("harbor", run.id)


def test_review_is_bound_to_code_and_current_intent(tmp_path: Path) -> None:
    execution = fixture(tmp_path)
    run = result(execution, process=False)
    with pytest.raises(ApplicationError, match="exact current candidate"):
        results = Results(execution.workspace)
        version = results.page("harbor", run.task_id).items[0]
        results.review(
            "harbor",
            version.id,
            ResultReview(
                expected_revision=version.revision,
                candidate_commit=BASE,
                action="approve",
                note="",
                author="human",
            ),
        )
    task = execution.workspace.task("harbor", run.task_id)
    with pytest.raises(ApplicationError, match="manual progress"):
        execution.workspace.record_progress(
            "harbor", task.id, TaskProgress(expected_revision=task.revision, status="done")
        )
    execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, body="Changed requirement")
    )
    validate_report(execution)
    assert (
        Results(execution.workspace).page("harbor", run.task_id).items[0].problem_code
        == "assignment_changed"
    )
    results = Results(execution.workspace)
    version = results.page("harbor", run.task_id).items[0]
    results.review(
        "harbor",
        version.id,
        ResultReview(
            expected_revision=version.revision,
            candidate_commit=RESULT,
            action="request_changes",
            note="Implement the updated requirement",
            author="human",
        ),
    )
    upcoming = execution.workspace.task("harbor", task.id)
    assert upcoming.status == "up_next" and upcoming.publication_status == "draft"
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
    execution.workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            stages=fixture_stage_change(execution.workspace, "harbor", task.id),
            completion="report",
            expected_revision=upcoming.revision,
        ),
    )
    revised = execution.claim("harbor", BASE, {BASE: set()})
    assert revised and revised.predecessor_id == run.id and revised.base_commit == RESULT
    assert revised.feedback == "Implement the updated requirement"
    with pytest.raises(ApplicationError, match="late result"):
        execution.finish("harbor", run.id, "in_review", result=run.result, commit=BASE)


def test_report_prerequisite_does_not_require_code_availability(tmp_path: Path) -> None:
    execution = fixture(tmp_path, count=2)
    workspace = execution.workspace
    second = workspace.task("harbor", "task-1")
    second = workspace.edit_task(
        "harbor", second.id, TaskEdit(expected_revision=second.revision, dependencies=["task-0"])
    )
    workspace.publish_task(
        "harbor",
        second.id,
        TaskPublish(
            stages=fixture_stage_change(workspace, "harbor", second.id),
            completion="report",
            expected_revision=second.revision,
        ),
    )
    run = result(execution)
    accepted = execution.get("harbor", run.id)
    assert not accepted.code_available
    assert workspace.task("harbor", run.task_id).status == "done"
    dependent = execution.claim("harbor", BASE, {BASE: set()})
    assert dependent and dependent.task_id == "task-1"


def test_failure_retry_and_restart_never_automatically_relaunch(tmp_path: Path) -> None:
    from flowfield.work_state import task_state

    execution = fixture(tmp_path, count=2)

    def state_label():
        with execution.workspace.connection() as db:
            return task_state(execution.workspace, db, "harbor", "task-0").label

    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.restart()
    assert not execution.settings("harbor").enabled
    unknown = execution.get("harbor", run.id)
    assert unknown.status == "uncertain" and len(execution.active()) == 1
    with pytest.raises(ApplicationError):
        execution.worker_access("harbor", run.id)
    execution.stop_requested("harbor", run.id, RunAction(expected_revision=unknown.revision))
    stopped = execution.finish("harbor", run.id, "stopped")
    assert not execution.active()
    assert state_label() == "Work stopped"
    execution.retry("harbor", run.id, RunAction(expected_revision=stopped.revision))
    assert state_label() == "Queue paused"
    assert execution.claim("harbor", BASE, {BASE: set()}) is None  # queue stays paused
    settings = execution.settings("harbor")
    execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    assert state_label() == "Queued"
    retry = execution.claim("harbor", BASE, {BASE: set()})
    assert retry and retry.predecessor_id == run.id and retry.environment_id != run.environment_id
    assert state_label() == "Preparing worker"


def test_usage_replay_is_not_added_and_history_is_paged(tmp_path: Path) -> None:
    execution = fixture(tmp_path, count=2, cap=2)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    usage = Usage(input_tokens=100, cached_input_tokens=60, output_tokens=10, total_tokens=110)
    execution.usage("harbor", run.id, usage)
    execution.usage("harbor", run.id, usage)
    execution.usage("harbor", run.id, Usage(total_tokens=50))
    assert execution.get("harbor", run.id).usage.total_tokens == 110
    execution.claim("harbor", BASE, {BASE: set()})
    page = execution.page("harbor", limit=1)
    assert len(page.items) == 1 and page.next_before
    assert len(execution.page("harbor", before=page.next_before).items) == 1


def test_followup_checks_prerequisites_in_its_own_base(tmp_path: Path) -> None:
    execution = fixture(tmp_path, count=2)
    prerequisite = result(execution)
    assert execution.workspace.task("harbor", prerequisite.task_id).status == "done"
    # Supply an accepted code prerequisite to this transactional claim test.
    # End-to-end code approval/delivery is tested using real Git in test_results.
    with execution.workspace.connection(write=True) as db:
        captured = execution._run(db, "harbor", prerequisite.id)
        captured.completion = "code"
        execution._save(db, captured)
    worker = execution.claim("harbor", BASE, {BASE: set()})
    assert worker and worker.task_id == "task-1"
    execution.started("harbor", worker.id)
    earlier_code = "c" * 40
    worker = execution.finish(
        "harbor",
        worker.id,
        "in_review",
        commit=earlier_code,
        result=WorkerResult(summary="Independent first result", checks="Fixture checks"),
    )
    task = execution.workspace.task("harbor", worker.task_id)
    task = execution.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, dependencies=[prerequisite.task_id]),
    )
    results = Results(execution.workspace)
    version = results.page("harbor", worker.task_id).items[0]
    results.review(
        "harbor",
        version.id,
        ResultReview(
            expected_revision=version.revision,
            candidate_commit=earlier_code,
            action="request_changes",
            note="Use the new prerequisite",
            author="human",
        ),
    )
    task = execution.workspace.task("harbor", task.id)
    execution.workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            stages=fixture_stage_change(execution.workspace, "harbor", task.id),
            completion="report",
            expected_revision=task.revision,
        ),
    )
    with pytest.raises(ApplicationError, match="prerequisite code"):
        execution.claim("harbor", RESULT, {RESULT: {RESULT}, earlier_code: set()})
    # A positive fact must concern the exact starting commit, not just project HEAD.
    followup = execution.claim("harbor", RESULT, {RESULT: {RESULT}, earlier_code: {RESULT}})
    assert followup and followup.base_commit == earlier_code
