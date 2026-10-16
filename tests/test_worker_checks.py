"""Exact saved-check permission, native grants and cleanup."""

import asyncio
import json
import os

import pytest
from execution_fixtures import BASE, fixture

from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction


def test_saved_checks_require_exact_human_permission_and_cleanup(tmp_path):
    from pydantic import ValidationError

    from flowfield.adapters.git_workspace import baseline, git
    from flowfield.adapters.local_execution import LocalHost
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
    runner = WorkerChecks(execution, run, environment)
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


def test_check_permission_read_releases_a_pending_writer(tmp_path, monkeypatch):
    """A second read connection would deadlock the writer behind the first read."""
    import sqlite3
    import time
    from threading import Event, Thread

    from flowfield.worker_checks import CheckPermissions

    store = fixture(tmp_path).workspace
    original = store._project
    waiting = Event()
    errors = []

    def write():
        try:
            with sqlite3.connect(store.database, timeout=2) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE projects SET description='Concurrent update' WHERE id='harbor'")
                waiting.set()
                db.commit()
        except Exception as error:
            errors.append(error)

    writer = Thread(target=write)
    first = True

    def project(db, identity):
        nonlocal first
        value = original(db, identity)
        if first:
            first = False
            writer.start()
            assert waiting.wait(1)
            # Wait for SQLite's pending-write lock, not an arbitrary timing delay.
            deadline = time.monotonic() + 1
            while True:
                probe = sqlite3.connect(store.database, timeout=0)
                try:
                    probe.execute("SELECT name FROM projects").fetchall()
                except sqlite3.OperationalError as error:
                    assert "locked" in str(error)
                    break
                finally:
                    probe.close()
                assert time.monotonic() < deadline
                time.sleep(0.001)
        return value

    monkeypatch.setattr(store, "_project", project)
    try:
        assert not CheckPermissions(store).get("harbor").enabled
    finally:
        writer.join(3)
    assert not writer.is_alive() and not errors
    assert store.project("harbor").description == "Concurrent update"


def test_check_permission_native_grant_is_exact_and_revocable(tmp_path):
    from flowfield.adapters.agent_contract import ManagedToolCall, PermissionRequest
    from flowfield.worker_checks import CheckPermissions, WorkerChecks

    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    runner = WorkerChecks(execution, run, None)
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


@pytest.mark.parametrize("capture_failure", [False, True])
def test_stop_cancels_owned_check_and_resumes_exact_preserved_work(
    tmp_path, monkeypatch, capture_failure
):
    from fake_worker import FakeWorker

    from flowfield.adapters.git_workspace import baseline, git
    from flowfield.integration_models import IntegrationConfig
    from flowfield.supervisor import Supervisor
    from flowfield.worker_checks import CheckPermissionEdit, CheckPermissions

    class CheckingWorker(FakeWorker):
        async def run(self, model, effort, prompt, tools):
            (self.cwd / "work.txt").write_text("preserve this")
            await service.check_jobs[run.id].call()
            return {"status": "completed"}

    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", CheckingWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda _: "fixture")
    execution = fixture(tmp_path)
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

    async def stop_running_check():
        job = asyncio.create_task(service._execute(run, repo))
        service.jobs[run.id] = job
        try:
            async with asyncio.timeout(10):
                while not execution.local(run.id).get("check_process"):
                    await asyncio.sleep(0.01)
                active = execution.get("harbor", run.id)
                await service.stop("harbor", run.id, RunAction(expected_revision=active.revision))
                await job
        finally:
            if not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
            service.jobs.pop(run.id, None)

    asyncio.run(stop_running_check())
    if capture_failure:
        uncertain = execution.get("harbor", run.id)
        assert uncertain.status == "uncertain" and not uncertain.checkpoint_commit
        asyncio.run(service.stop("harbor", run.id, RunAction(expected_revision=uncertain.revision)))
    stopped = execution.get("harbor", run.id)
    assert stopped.status == "stopped", stopped.problem
    assert stopped.checkpoint_commit and stopped.checkpoint_commit != base
    assert not execution.local(run.id).get("check_process")
    execution.retry("harbor", run.id, RunAction(expected_revision=stopped.revision))
    resumed = execution.claim("harbor", base, service._available("harbor", repo, base))
    assert resumed.input_base_commit == stopped.checkpoint_commit
    assert resumed.base_commit == base
    assert git(repo, "show", resumed.input_base_commit + ":work.txt").decode() == "preserve this"
