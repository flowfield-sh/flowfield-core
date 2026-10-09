"""Recovery through normal operations with real Git and isolated runtimes; no models."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_results import approve, current, fixture

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import contains, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.application import TaskReconcile
from flowfield.attention import attention_page
from flowfield.errors import ApplicationError
from flowfield.execution_history import ExecutionHistory
from flowfield.execution_models import QueueEdit, RunAction, WorkerResult
from flowfield.integration_models import IntegrationConfig


def action(version):
    return RunAction(expected_revision=version.revision, author="human")


def target_change(service, repo, base, name="target.txt", content="target change\n"):
    env = LocalHost(os.environ).prepare(
        service.workspace.directory, repo, "target-" + name.replace(".", "-"), base
    )
    (env.checkout / name).write_text(content)
    commit, _ = env.snapshot(base)
    git(repo, "merge", "--ff-only", commit)
    return commit


def claim_correction(service, repo):
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    head = service.integrations.head("harbor")
    run = service.execution.claim("harbor", head, service._available("harbor", repo, head))
    assert run
    assert run.correction and run.model == "fixture" and run.effort == "low"
    return run


def finish_correction(service, repo, run, content="both changes resolved\n"):
    service.execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(service.workspace.directory, repo, run.id, run.base_commit)
    (env.checkout / "result.txt").write_text(content)
    commit, _ = env.snapshot(run.base_commit)
    service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        commit=commit,
        result=WorkerResult(summary="Resolved both inputs", checks="Test fixture"),
    )
    service.results.process("harbor")
    return current(service)


def test_conflict_correction_retains_both_inputs_and_requires_new_approval(tmp_path):
    service, repo, original = fixture(tmp_path)
    target = target_change(service, repo, original.base_commit, "result.txt", "target version\n")
    (repo / "README.md").write_text("human dirty work")
    service.results.process("harbor")
    blocked = current(service)
    assert blocked.problem_code == "integration_conflict"
    assert service.workspace.board("harbor").needs_you_count == 1
    assert "result.txt" in blocked.problem
    closed = service.results.correct("harbor", blocked.id, action(blocked))
    assert closed.status == "changes_requested"
    assert not service.execution.settings("harbor").enabled
    run = claim_correction(service, repo)
    assert run.correction.number == 1
    assert contains(repo, target, run.base_commit)
    assert contains(repo, original.result_commit, run.base_commit)
    assert b"<<<<<<<" in git(repo, "show", f"{run.base_commit}:result.txt")
    assignment = service.execution.assignment("harbor", run.id)
    evidence = json.loads(assignment["correction"])
    assert evidence["source_commit"] == original.result_commit
    assert "result.txt" in evidence["details"]
    ready = finish_correction(service, repo, run)
    assert ready.version == 2 and ready.status == "ready", ready.problem
    assert service.workspace.board("harbor").needs_you_count == 1
    assert not ready.approved_at
    assert service.integrations.head("harbor") == target
    approve(service, ready)
    service.results.process("harbor")
    assert current(service).problem_code == "checkout_dirty"
    assert (repo / "README.md").read_text() == "human dirty work"
    (tmp_path / "preserved-readme").write_text((repo / "README.md").read_text())
    git(repo, "restore", "--worktree", "README.md")
    service.results.retry_delivery("harbor", ready.id, action(current(service)))
    service.results.process("harbor")
    assert current(service).status == "delivered"
    assert service.workspace.board("harbor").needs_you_count == 0
    assert {
        item.result_version for item in attention_page(service.workspace, "harbor", "history").items
    } == {1, 2}
    assert service.workspace.task("harbor", "work").status == "done"
    assert (tmp_path / "preserved-readme").read_text() == "human dirty work"
    history = ExecutionHistory(service.workspace).page("harbor", "work")
    assert any(item.purpose == "Correct result" for item in history.items)


def test_failed_checks_correction_cap_and_no_automatic_model_loop(tmp_path):
    service, repo, _ = fixture(tmp_path, checks=["exit 7"])
    service.results.process("harbor")
    for number in (1, 2):
        blocked = current(service)
        assert blocked.problem_code == "checks_failed"
        service.results.correct("harbor", blocked.id, action(blocked))
        run = claim_correction(service, repo)
        assert run.correction.number == number
        assert '"exit_code": 7' in run.correction.details
        blocked = finish_correction(service, repo, run)
        assert blocked.status == "blocked" and not blocked.approved_at
        before = len(service.execution.page("harbor").items)
        service.results.process("harbor")
        assert len(service.execution.page("harbor").items) == before
    with pytest.raises(ApplicationError, match="Two corrections"):
        service.results.correct("harbor", blocked.id, action(blocked))


def test_cancel_during_validation_retains_evidence_and_ignores_late_ready(tmp_path, monkeypatch):
    service, repo, run = fixture(tmp_path)
    entered, release = Event(), Event()
    actual = gitops.run_checks

    def check(env, commands, timeout=60):
        if commands:
            entered.set()
            assert release.wait(10)
        return actual(env, commands, timeout)

    monkeypatch.setattr(gitops, "run_checks", check)
    with ThreadPoolExecutor() as pool:
        work = pool.submit(service.results.process, "harbor")
        assert entered.wait(10)
        pending = current(service)
        cancelled = service.results.cancel("harbor", pending.id, action(pending))
        assert cancelled.status == "cancelled"
        release.set()
        work.result(10)
    assert current(service).status == "cancelled"
    assert service.integrations.get("harbor", pending.integration_id).checks
    assert service.integrations.head("harbor") == run.base_commit
    with pytest.raises(ApplicationError, match="successful candidate"):
        approve(service, current(service))
    fresh = service.results.reprepare("harbor", cancelled.id, action(current(service)))
    assert fresh.version == 2 and not fresh.approved_at
    service.results.process("harbor")
    assert current(service).status == "ready"


def test_changed_checkout_then_explicit_reprepare(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    git(repo, "switch", "main")
    service.results.process("harbor")
    blocked = current(service)
    assert blocked.status == "stale"
    assert blocked.problem_code == "checkout_branch_changed"
    assert service.integrations.head("harbor") == run.base_commit
    git(repo, "switch", "integration")
    new = service.results.reprepare("harbor", blocked.id, action(blocked))
    service.results.process("harbor")
    assert not current(service).approved_at and new.version == 2
    approve(service, current(service))
    service.results.process("harbor")
    assert current(service).status == "delivered"


def test_historical_done_revalidates_external_advance_without_moving_git(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    service.results.process("harbor")
    head = target_change(service, repo, service.integrations.head("harbor"))
    service.integrations.refresh_availability("harbor")
    assert not service.execution.get("harbor", run.id).code_available
    assert service.workspace.task("harbor", "work").status == "done"
    version = current(service)
    assert any(
        i.id == version.id for i in attention_page(service.workspace, "harbor", "action").items
    )
    checked = service.results.revalidate("harbor", version.id, action(version))
    assert checked.approved_at == version.approved_at
    assert checked.integration_id == version.integration_id
    assert service.integrations.get("harbor", checked.availability_id).status == "integrated"
    assert service.integrations.head("harbor") == head
    assert service.execution.get("harbor", run.id).code_available
    assert not attention_page(service.workspace, "harbor", "action").items
    git(repo, "update-ref", "refs/heads/integration", run.base_commit)
    service.integrations.refresh_availability("harbor")
    with pytest.raises(ApplicationError, match="lost delivered code"):
        service.results.revalidate("harbor", checked.id, action(checked))
    assert service.workspace.task("harbor", "work").status == "done"


def test_runtime_setup_and_failed_revalidation_preserve_completion(tmp_path):
    service, repo, run = fixture(tmp_path)
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch="integration",
            setup_commands=['touch "$FLOWFIELD_RUNTIME_DIR/runtime-ready"'],
            checks=['test -f "$FLOWFIELD_RUNTIME_DIR/runtime-ready" && test -f result.txt'],
            check_timeout_seconds=180,
        ),
    )
    service.results.process("harbor")
    assert current(service).status == "ready", current(service).problem
    record = service.integrations.get("harbor", current(service).integration_id)
    assert record.setup_checks[0].exit_code == 0
    approve(service, current(service))
    service.results.process("harbor")
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch="integration",
            checks=["exit 9"],
        ),
    )
    version = current(service)
    checked = service.results.revalidate("harbor", version.id, action(version))
    assert service.integrations.get("harbor", checked.availability_id).checks[0].exit_code == 9
    assert not service.execution.get("harbor", run.id).code_available
    assert service.workspace.task("harbor", "work").status == "done"


def test_changed_target_requires_explicit_reconciliation_before_correction(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch="new-target",
            create_from="main",
            checks=["test -f result.txt"],
        ),
    )
    service.integrations.refresh_availability("harbor")
    version = current(service)
    with pytest.raises(ApplicationError, match="Reconcile"):
        service.results.correct("harbor", version.id, action(version))
    task = service.workspace.task("harbor", "work")
    service.workspace.reconcile_task(
        "harbor",
        task.id,
        TaskReconcile(
            expected_revision=task.revision,
            completion="code",
            note="Deliver to new-target",
        ),
    )
    service.results.correct("harbor", version.id, action(version))
    correction = claim_correction(service, repo)
    assert correction.target_branch == "new-target"
    ready = finish_correction(service, repo, correction)
    assert ready.status == "ready" and ready.target_branch == "new-target"
    assert not ready.approved_at


def test_setup_failure_does_not_launch_model_and_retains_location(tmp_path, monkeypatch):
    service, repo, _ = fixture(tmp_path, checks=["exit 7"])
    service.results.process("harbor")
    version = current(service)
    service.results.correct("harbor", version.id, action(version))
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch="integration",
            setup_commands=["exit 8"],
            checks=["true"],
        ),
    )
    correction = claim_correction(service, repo)

    async def forbidden(*args):
        pytest.fail("Setup failure must not launch a model")

    from test_supervisor import FakeWorker

    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", FakeWorker)
    monkeypatch.setattr(FakeWorker, "run", forbidden, raising=False)
    asyncio.run(service._execute(correction, repo))
    failed = service.execution.get("harbor", correction.id)
    assert failed.status == "failed"
    assert failed.setup_checks[0].exit_code == 8
    assert service.location("harbor", failed.id).workspace


def test_failed_correction_retry_consumes_remaining_attempt(tmp_path):
    service, repo, _ = fixture(tmp_path, checks=["exit 7"])
    service.results.process("harbor")
    version = current(service)
    service.results.correct("harbor", version.id, action(version))
    first = claim_correction(service, repo)
    failed = service.execution.finish("harbor", first.id, "failed", problem="Fixture failure")
    service.execution.retry("harbor", failed.id, action(failed))
    second = claim_correction(service, repo)
    assert second.correction.number == 2
    failed = service.execution.finish("harbor", second.id, "failed", problem="Fixture failure")
    with pytest.raises(ApplicationError, match="Two corrections"):
        service.execution.retry("harbor", failed.id, action(failed))


def test_answer_continuation_preserves_correction_budget_but_retry_consumes_it(tmp_path):
    from flowfield.questions import QuestionAnswer, QuestionCreate, Questions

    service, repo, _ = fixture(tmp_path, checks=["exit 7"])
    service.results.process("harbor")
    blocked = current(service)
    service.results.correct("harbor", blocked.id, action(blocked))
    run = claim_correction(service, repo)
    service.execution.started("harbor", run.id)
    question = service.execution.ask_question(
        "harbor",
        run.id,
        QuestionCreate(
            task_id=run.task_id,
            question="Which behavior should the fix preserve?",
            context="Two expected behaviors conflict.",
            recommendation="Preserve the existing behavior.",
            blocking_scope="Product behavior",
        ),
    )
    service.execution.finish(
        "harbor", run.id, "waiting_for_input", input_checkpoint=run.base_commit
    )
    Questions(service.workspace).answer(
        "harbor",
        question.id,
        QuestionAnswer(
            expected_revision=question.revision, answer="Preserve the existing behavior."
        ),
    )
    successor = claim_correction(service, repo)
    assert successor.correction.number == run.correction.number == 1
    failed = service.execution.finish("harbor", successor.id, "failed", problem="Worker failed")
    service.execution.retry("harbor", failed.id, action(failed))
    retry = claim_correction(service, repo)
    assert retry.correction.number == 2
    assert json.loads(service.execution.assignment("harbor", retry.id)["correction"])["number"] == 2
    failed = service.execution.finish("harbor", retry.id, "failed", problem="Worker failed again")
    with pytest.raises(ApplicationError, match="Two corrections"):
        service.execution.retry("harbor", failed.id, action(failed))
