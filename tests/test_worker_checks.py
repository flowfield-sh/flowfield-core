"""Default saved checks, exact managed-tool grants and cleanup."""

import asyncio
import json
import os

import pytest
from execution_fixtures import BASE, fixture

from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction


def test_saved_checks_are_available_by_default_and_remain_frozen(tmp_path):
    from pydantic import ValidationError

    from flowfield.adapters.git_workspace import baseline, git
    from flowfield.adapters.local_execution import LocalHost
    from flowfield.integration import Integrations
    from flowfield.integration_models import IntegrationConfig
    from flowfield.worker_checks import WorkerChecks
    from flowfield.worker_tools import WorkerBridge

    execution = fixture(tmp_path)
    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / "check.sh").write_text(
        'printf x >> "$FLOWFIELD_RUNTIME_DIR/invocations"\n'
        "echo configured-check; test -f result.txt\n"
    )
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
    execution.save_local(run.id, {"runtime": str(environment.runtime)})
    (environment.checkout / "result.txt").write_text("new work")
    runner = WorkerChecks(execution, run, environment)
    bridge = WorkerBridge(execution, run, object(), runner.call)

    async def exercise():
        with pytest.raises(ValidationError):
            await bridge.call("run_project_checks", {"command": "touch escape"})
        report = json.loads(await bridge.call("run_project_checks", {}))
        assert report["checks"][0]["exit_code"] == 0
        assert report["checks"][0]["output"] == "configured-check\n"
        section = report["checks"][0]["log_section"]
        first = json.loads(await bridge.call("read_context", {"section": section, "limit": 5}))
        chunks = [first["text"]]
        while first["next_request"]:
            first = json.loads(await bridge.call("read_context", first["next_request"]))
            chunks.append(first["text"])
        assert "".join(chunks) == "configured-check\n"
        assert (environment.runtime / "invocations").read_text() == "x"
        with pytest.raises(ApplicationError, match="No retained log"):
            await bridge.call("read_context", {"section": "project_check_log:" + "f" * 32})
        assert not execution.local(run.id).get("check_process")
        integration.configure(
            "harbor",
            IntegrationConfig(
                expected_revision=saved.revision, target_branch="main", checks=["echo changed"]
            ),
        )
        report = json.loads(await bridge.call("run_project_checks", {}))
        assert report["checks"][0]["output"] == "configured-check\n"
        assert (environment.runtime / "invocations").read_text() == "xx"
        await runner.close()

    asyncio.run(exercise())


def test_managed_check_grant_is_exact_and_requires_an_active_run(tmp_path):
    from flowfield.adapters.agent_contract import ManagedToolCall, PermissionRequest
    from flowfield.worker_checks import WorkerChecks

    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    runner = WorkerChecks(execution, run, None)
    seen = []

    async def fallback(value):
        seen.append(value)
        return "deny"

    async def exercise():
        options = (("once", "Allow once", "allow_once"), ("always", "Always", "allow_always"))
        exact = PermissionRequest(
            "id",
            "A title proves no authority",
            options,
            managed_tool=ManagedToolCall("owned", "run_project_checks", {}),
        )
        handler = runner.permission("owned", fallback)
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
        from unittest.mock import patch

        with patch.object(
            execution, "worker_access", side_effect=ApplicationError("closed", "closed")
        ):
            assert await handler(exact) == "deny"
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
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=1,
            target_branch="main",
            checks=["sleep 60"],
            check_timeout_seconds=120,
        ),
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
    resumed = execution.claim("harbor", base, service._available("harbor", "harbor", repo, base))
    assert resumed.input_base_commit == stopped.checkpoint_commit
    assert resumed.base_commit == base
    assert git(repo, "show", resumed.input_base_commit + ":work.txt").decode() == "preserve this"
