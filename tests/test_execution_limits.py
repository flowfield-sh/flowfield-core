"""Task-wide limits across waits, retries, restart, revocation and owned commands."""

import asyncio
import json
import os

import pytest
from test_execution import BASE, fixture
from test_input_continuation import answer, question

from flowfield.application import Workspace
from flowfield.attention import attention_page
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_limits import ExecutionLimits, LimitExtension, PolicyEdit
from flowfield.execution_models import RunAction
from flowfield.questions import Questions
from flowfield.worker_settings import workspace_id


def settings(execution, seconds=10, corrections=1):
    limits = ExecutionLimits(execution.workspace)
    with execution.workspace.connection() as db:
        owner = workspace_id(db, "harbor")
    limits.configure(
        owner,
        PolicyEdit(
            expected_revision=limits.defaults(owner).revision,
            active_seconds=seconds,
            corrections=corrections,
        ),
    )
    return limits


def test_active_wait_answer_and_restart_share_allowance(tmp_path):
    execution = fixture(tmp_path)
    limits = settings(execution)
    run, q = question(execution)
    limits.clock("harbor", run.id, active=True, at=100)
    limits.clock("harbor", run.id, active=False, at=103)
    limits.clock("harbor", run.id, active=None, at=10000)
    assert limits.get("harbor", run.task_id).active_seconds_used == 3
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=BASE)
    answer(Questions(execution.workspace), q)
    continued = execution.claim("harbor", BASE, {BASE: set()})
    assert continued and continued.input_question_id == q.id
    assert limits.get("harbor", run.task_id).corrections_used == 0
    execution.started("harbor", continued.id)
    limits.clock("harbor", continued.id, active=True, at=10001)
    assert not limits.clock("harbor", continued.id, active=None, at=10009)
    execution.finish("harbor", continued.id, "stopped")
    reopened = Execution(Workspace(execution.workspace.directory))
    assert reopened.claim("harbor", BASE, {BASE: set()}) is None
    allowance = ExecutionLimits(reopened.workspace).get("harbor", run.task_id)
    assert allowance.blocked == "active_time" and allowance.active_seconds_used == 11
    inbox = attention_page(reopened.workspace, "harbor", "action")
    assert [i.kind for i in inbox.items] == ["execution_limit"]
    request = LimitExtension(
        expected_revision=allowance.revision, active_seconds=20, note="Continue the agreed outcome"
    )
    limits.extend("harbor", run.task_id, request)
    assert not attention_page(reopened.workspace, "harbor", "action").items
    with pytest.raises(ApplicationError):
        limits.extend("harbor", run.task_id, request)
    next_run = reopened.claim("harbor", BASE, {BASE: set()})
    assert next_run and "Continue the agreed outcome" in next_run.feedback
    assert limits.get("harbor", run.task_id).active_seconds_used == 11


def test_followups_block_at_claim_and_defaults_do_not_reset_existing_task(tmp_path):
    execution = fixture(tmp_path)
    limits = settings(execution, corrections=1)
    for _ in range(2):
        run = execution.claim("harbor", BASE, {BASE: set()})
        assert run
        execution.finish("harbor", run.id, "failed")
        run = execution.get("harbor", run.id)
        execution.retry("harbor", run.id, RunAction(expected_revision=run.revision))
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
    value = limits.get("harbor", run.task_id)
    assert value.corrections_used == 1 and value.blocked == "corrections"
    settings(execution, seconds=100, corrections=10)
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
    value = limits.extend(
        "harbor",
        run.task_id,
        LimitExtension(
            expected_revision=value.revision, corrections=1, note="One focused correction"
        ),
    )
    assert value.active_seconds_allowed == 10 and value.corrections_allowed == 2
    assert execution.claim("harbor", BASE, {BASE: set()})


def test_crash_charges_unknown_interval_and_requires_process_reconciliation(tmp_path):
    execution = fixture(tmp_path)
    limits = settings(execution)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    limits.clock("harbor", run.id, active=True, at=1)
    execution.restart()
    value = limits.get("harbor", run.task_id)
    assert value.active_seconds_used == 10 and value.earlier_usage_unknown
    assert value.blocked == "active_time"
    assert execution.get("harbor", run.id).active_since is None
    with pytest.raises(ApplicationError, match="stopped"):
        limits.extend(
            "harbor",
            run.task_id,
            LimitExtension(expected_revision=value.revision, active_seconds=10, note="Resume"),
        )


def test_active_owner_stops_once_and_permission_wait_does_not_consume(tmp_path):
    from flowfield.adapters.agent_contract import PermissionRequest
    from flowfield.execution_runtime import ActiveExecution

    execution = fixture(tmp_path)
    limits = settings(execution, seconds=1)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    active = ActiveExecution(limits, run)

    async def exercise():
        async def wait(_):
            await asyncio.sleep(1.1)
            return "yes"

        with pytest.raises(ApplicationError, match="allowance reached"):
            async with active:
                await active.permission(wait)(
                    PermissionRequest(tool_id="test", title="Test", options=[])
                )
                assert limits.get("harbor", run.task_id).active_seconds_used < 0.1
                await asyncio.sleep(3)

    asyncio.run(exercise())
    assert limits.get("harbor", run.task_id).blocked == "active_time"


def test_saved_checks_require_exact_human_permission_and_cleanup(tmp_path):
    from pydantic import ValidationError

    from flowfield.adapters.git_workspace import baseline, git
    from flowfield.adapters.local_execution import LocalHost
    from flowfield.execution_runtime import ActiveExecution
    from flowfield.integration import Integrations
    from flowfield.integration_models import IntegrationConfig
    from flowfield.worker_checks import CheckPermissionEdit, CheckPermissions, WorkerChecks
    from flowfield.worker_tools import WorkerBridge

    execution = fixture(tmp_path)
    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / "check.sh").write_text("echo configured-check; test -f result.txt\n")
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
    integration = Integrations(execution.workspace)
    saved = integration.configure(
        "harbor",
        IntegrationConfig(expected_revision=1, target_branch="main", checks=["sh check.sh"]),
    )
    base = baseline(repo)
    run = execution.claim("harbor", base, {base: set()})
    assert run
    execution.started("harbor", run.id)
    environment = LocalHost(os.environ).prepare(execution.workspace.directory, repo, run.id, base)
    (environment.checkout / "result.txt").write_text("new work")
    active = ActiveExecution(ExecutionLimits(execution.workspace), run)
    runner = WorkerChecks(execution, run, environment, active)
    bridge = WorkerBridge(execution, run, object(), runner.call)
    permissions = CheckPermissions(execution.workspace)

    async def exercise():
        with pytest.raises(ApplicationError, match="human permission"):
            await bridge.call("run_project_checks", {})
        grant = permissions.configure(
            "harbor",
            CheckPermissionEdit(
                expected_revision=1, settings_revision=saved.revision, enabled=True
            ),
        )
        with pytest.raises(ValidationError):
            await bridge.call("run_project_checks", {"command": "touch escape"})
        report = json.loads(await bridge.call("run_project_checks", {}))
        assert report["checks"][0]["exit_code"] == 0
        assert report["checks"][0]["output"] == "configured-check\n"
        assert not execution.local(run.id).get("check_process")
        permissions.configure(
            "harbor",
            CheckPermissionEdit(
                expected_revision=grant.revision, settings_revision=saved.revision, enabled=False
            ),
        )
        with pytest.raises(ApplicationError):
            await bridge.call("run_project_checks", {})
        grant = permissions.get("harbor")
        permissions.configure(
            "harbor",
            CheckPermissionEdit(
                expected_revision=grant.revision, settings_revision=saved.revision, enabled=True
            ),
        )
        integration.configure(
            "harbor",
            IntegrationConfig(
                expected_revision=saved.revision, target_branch="main", checks=["echo changed"]
            ),
        )
        assert not permissions.get("harbor").enabled
        with pytest.raises(ApplicationError):
            await bridge.call("run_project_checks", {})
        await runner.close()

    asyncio.run(exercise())


def test_supported_upgrade_preserves_work_without_inventing_usage(tmp_path):
    import sqlite3

    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.finish("harbor", run.id, "failed")
    # Restore the schema-71 shape, retaining the authored task and historical run.
    with sqlite3.connect(execution.workspace.database) as db:
        for table in ("execution_policies", "task_execution_limits", "worker_check_grants"):
            db.execute(f"DROP TABLE {table}")
        db.execute("DELETE FROM schema_migrations WHERE version=72")
        db.execute("PRAGMA user_version=71")
    upgraded = Execution(Workspace(execution.workspace.directory))
    latest = upgraded.get("harbor", run.id)
    assert latest.status == "failed" and latest.active_seconds == 0
    upgraded.retry("harbor", run.id, RunAction(expected_revision=latest.revision))
    continued = upgraded.claim("harbor", BASE, {BASE: set()})
    assert continued
    limit = ExecutionLimits(upgraded.workspace).get("harbor", run.task_id)
    assert limit.earlier_usage_unknown and limit.active_seconds_used == 0
    assert limit.corrections_used == 1


@pytest.mark.parametrize("capture_failure", [False, True])
def test_exhaustion_cancels_owned_check_and_resumes_exact_preserved_work(
    tmp_path, monkeypatch, capture_failure
):
    from test_supervisor import FakeWorker

    from flowfield.adapters.git_workspace import baseline, git
    from flowfield.integration_models import IntegrationConfig
    from flowfield.supervisor import Supervisor
    from flowfield.worker_checks import CheckPermissionEdit, CheckPermissions

    class CheckingWorker(FakeWorker):
        async def run(self, model, effort, prompt, tools):
            (self.cwd / "work.txt").write_text("preserve this")
            await self.on_tool("run_project_checks", {})
            return {"status": "completed"}

    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", CheckingWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda _: "fixture")
    execution = fixture(tmp_path)
    limits = settings(execution, seconds=1)
    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / "base").write_text("base")
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
    service = Supervisor(execution.workspace)
    config = service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=1,
            target_branch="main",
            checks=["sleep 60"],
            check_timeout_seconds=120,
        ),
    )
    CheckPermissions(execution.workspace).configure(
        "harbor",
        CheckPermissionEdit(expected_revision=1, settings_revision=config.revision, enabled=True),
    )
    base = baseline(repo)
    run = execution.claim("harbor", base, {base: set()})
    if capture_failure:
        from flowfield.adapters.local_execution import LocalAttempt

        original = LocalAttempt.snapshot
        calls = 0

        def snapshot(self, base):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("Temporary fixture snapshot failure")
            return original(self, base)

        monkeypatch.setattr(LocalAttempt, "snapshot", snapshot)
    asyncio.run(asyncio.wait_for(service._execute(run, repo), 10))
    if capture_failure:
        uncertain = execution.get("harbor", run.id)
        assert uncertain.status == "uncertain" and not uncertain.checkpoint_commit
        asyncio.run(service.stop("harbor", run.id, RunAction(expected_revision=uncertain.revision)))
    stopped = execution.get("harbor", run.id)
    assert stopped.status == "stopped", stopped.problem
    assert stopped.checkpoint_commit and stopped.checkpoint_commit != base
    assert not execution.local(run.id).get("check_process")
    value = limits.get("harbor", run.task_id)
    assert value.blocked == "active_time"
    limits.extend(
        "harbor",
        run.task_id,
        LimitExtension(
            expected_revision=value.revision,
            active_seconds=10,
            note="Finish from the preserved changes",
        ),
    )
    resumed = execution.claim("harbor", base, service._available("harbor", repo, base))
    assert resumed.input_base_commit == stopped.checkpoint_commit
    assert resumed.base_commit == base
    assert git(repo, "show", resumed.input_base_commit + ":work.txt").decode() == "preserve this"


def test_check_permission_native_grant_is_exact_and_revocable(tmp_path):
    from flowfield.adapters.agent_contract import ManagedToolCall, PermissionRequest
    from flowfield.execution_runtime import ActiveExecution
    from flowfield.worker_checks import CheckPermissions, WorkerChecks

    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    runner = WorkerChecks(
        execution, run, None, ActiveExecution(ExecutionLimits(execution.workspace), run)
    )
    seen = []

    async def fallback(value):
        seen.append(value)
        return "deny"

    async def exercise():
        from unittest.mock import patch

        options = (("once", "Allow once", "allow_once"), ("always", "Always", "allow_always"))
        exact = PermissionRequest(
            "id",
            "A title proves no authority",
            options,
            managed_tool=ManagedToolCall("owned", "run_project_checks", {}),
        )
        handler = runner.permission("owned", fallback)
        with patch.object(CheckPermissions, "require", return_value=None):
            assert await handler(exact) == "once"
            for call in (
                None,
                ManagedToolCall("ambient", "run_project_checks", {}),
                ManagedToolCall("owned", "run_command", {}),
                ManagedToolCall("owned", "run_project_checks", {"command": "escape"}),
            ):
                assert (
                    await handler(PermissionRequest("id", exact.title, options, managed_tool=call))
                    == "deny"
                )
            assert len(seen) == 4
        assert await handler(exact) == "deny"  # No actual saved permission exists.
        assert len(seen) == 5

    asyncio.run(exercise())


def test_accounting_failure_cancels_the_active_owner(tmp_path, monkeypatch):
    from flowfield.execution_runtime import ActiveExecution

    execution = fixture(tmp_path)
    limits = settings(execution)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    original = limits.clock

    def broken(project, run_id, *, active, **kwargs):
        if active is None:
            raise OSError("Fixture accounting failure")
        return original(project, run_id, active=active, **kwargs)

    monkeypatch.setattr(limits, "clock", broken)

    async def exercise():
        with pytest.raises(ApplicationError, match="could not be recorded"):
            async with ActiveExecution(limits, run):
                await asyncio.sleep(10)

    asyncio.run(asyncio.wait_for(exercise(), 2))


def test_question_captured_at_exhaustion_keeps_free_answer_continuation(tmp_path):
    execution = fixture(tmp_path)
    limits = settings(execution, seconds=1, corrections=0)
    run, q = question(execution)
    limits.clock("harbor", run.id, active=True, at=100)
    limits.clock("harbor", run.id, active=None, at=102)
    execution.finish("harbor", run.id, "stopped", input_checkpoint=BASE)
    value = limits.get("harbor", run.task_id)
    limits.extend(
        "harbor",
        run.task_id,
        LimitExtension(
            expected_revision=value.revision,
            active_seconds=10,
            corrections=0,
            note="Use the answer to finish.",
        ),
    )
    assert execution.get("harbor", run.id).status == "waiting_for_input"
    answer(Questions(execution.workspace), q)
    continued = execution.claim("harbor", BASE, {BASE: set()})
    assert continued and continued.input_question_id == q.id
    assert limits.get("harbor", run.task_id).corrections_used == 0
    assert "Use the answer to finish" in execution.assignment("harbor", continued.id)["feedback"]
