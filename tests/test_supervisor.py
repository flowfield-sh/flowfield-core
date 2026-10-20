"""Service lifecycle with a deterministic harness; never invokes a model."""

import asyncio

import pytest
from execution_fixtures import fixture
from fake_worker import FakeWorker

from flowfield.adapters.git_workspace import baseline, git
from flowfield.application import TaskPublish
from flowfield.execution_models import QueueEdit, RunAction, SettingsEdit
from flowfield.integration_models import IntegrationConfig
from flowfield.result_models import ResultReview
from flowfield.supervisor import Supervisor


def test_scheduler_failure_is_visible_without_automatic_replay(tmp_path, monkeypatch, caplog):
    service = Supervisor(fixture(tmp_path).workspace)
    calls = []

    async def broken():
        calls.append(True)
        raise RuntimeError("Discovery broke")

    monkeypatch.setattr(service, "_schedule_once", broken)
    with pytest.raises(RuntimeError, match="Discovery broke"):
        asyncio.run(service._schedule())
    assert len(calls) == 1
    assert "Scheduling stopped" in service.execution.settings("harbor").problem
    assert "restart" in service.execution.settings("harbor").problem.lower()
    assert "Discovery broke" in caplog.text


def test_managed_claim_result_review_and_restart(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("flowfield.supervisor.Supervisor.validate_agent_choice", AsyncMock())
    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", FakeWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture-process")
    execution = fixture(tmp_path)
    from flowfield.workspaces import WorkspaceEdit, Workspaces

    spaces = Workspaces(execution.workspace)
    spaces.edit(
        "harbor",
        WorkspaceEdit(expected_revision=spaces.get("harbor").revision, auto_start_queue=True),
    )
    repo = tmp_path / "harbor"
    git(repo, "init")
    (repo / "base.txt").write_text("base")
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
            runtime="local",
            expected_revision=1,
            target_branch="integration",
            create_from="HEAD",
            checks=["test -f result.txt"],
        ),
    )

    git(repo, "switch", "integration")
    task = execution.workspace.task("harbor", "task-0")
    execution.workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            completion="code",
            expected_revision=task.revision,
        ),
    )

    async def exercise():
        await service.start()
        assert execution.settings("harbor").enabled
        for _ in range(100):
            page = execution.page("harbor")
            if page.items and page.items[0].status == "in_review":
                break
            await asyncio.sleep(0.05)
        run = execution.page("harbor").items[0]
        assert run.status == "in_review" and run.usage.total_tokens is None
        from flowfield.run_activity import RunActivity

        activity = RunActivity(execution.workspace).read("harbor", run.id)
        assert any('"result.txt": +1 / −0 lines' in entry.text for entry in activity.items)
        assert run.result_commit and baseline(repo) == run.base_commit
        assert service.location("harbor", run.id).workspace
        for _ in range(100):
            version = service.results.page("harbor", run.task_id).items[0]
            if version.status == "ready":
                break
            await asyncio.sleep(0.05)
        assert version.status == "ready", version.problem
        service.results.review(
            "harbor",
            version.id,
            ResultReview(
                expected_revision=version.revision,
                candidate_commit=version.candidate_commit,
                action="approve",
            ),
        )
        for _ in range(100):
            if service.results.get("harbor", version.id).status == "delivered":
                break
            await asyncio.sleep(0.05)
        assert service.workspace.task("harbor", run.task_id).status == "done"
        service.integrations.refresh_availability("harbor")
        assert service.execution.get("harbor", run.id).code_available
        await service.close()
        resumed = Supervisor(execution.workspace)
        await resumed.start()
        assert resumed.execution.settings("harbor").enabled
        assert resumed.execution.get("harbor", run.id).status == "accepted"
        await resumed.close()

    asyncio.run(exercise())


def test_recovery_does_not_confuse_harness_absence_with_tool_exit(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", "a" * 40, {"a" * 40: set()})
    execution.save_local(run.id, {"runtime_kind": "local", "setup_process": 12345})
    execution.restart()
    current = execution.get("harbor", run.id)
    service = Supervisor(execution.workspace)
    recovered = asyncio.run(
        service.stop("harbor", run.id, RunAction(expected_revision=current.revision))
    )
    assert recovered.status == "uncertain"
    assert "does not prove" in recovered.problem


def test_parallel_queue_capacity_pause_and_exact_delivery(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("flowfield.supervisor.Supervisor.validate_agent_choice", AsyncMock())
    """Two real worktrees, controlled harnesses, and the service's actual scheduler."""
    workers = []

    class ControlledWorker(FakeWorker):
        async def run(self, model, effort, prompt, tools):
            self.release = asyncio.Event()
            self.index = len(workers)
            workers.append(self)
            (self.cwd / f"result-{self.index}.txt").write_text(f"worker {self.index}\n")
            await self.release.wait()
            if self.stopping:
                return {"status": "interrupted"}
            await self.on_tool(
                "submit_result",
                {"outcome": "complete", "summary": "Independent change", "checks": "Fixture"},
            )
            return {"status": "completed"}

        async def stop(self):
            self.stopping = True
            if hasattr(self, "release"):
                self.release.set()
            return True

    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", ControlledWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture-process")
    execution = fixture(tmp_path, count=3, cap=2)
    from flowfield.browser import BrowserReads

    def board_statuses():
        return {
            task.id: task.status
            for task in BrowserReads(execution.workspace).workspace_board("harbor").tasks
        }

    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base")
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
            runtime="local",
            expected_revision=1,
            target_branch="integration",
            create_from="main",
            checks=["test -f base.txt"],
        ),
    )
    git(repo, "switch", "integration")
    base = baseline(repo)
    for index in range(3):
        task = execution.workspace.task("harbor", f"task-{index}")
        execution.workspace.publish_task(
            "harbor",
            task.id,
            TaskPublish(
                completion="code",
                expected_revision=task.revision,
            ),
        )

    def queue(enabled):
        settings = execution.settings("harbor")
        execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=enabled))

    def cap(value):
        settings = execution.settings("harbor")
        execution.configure(
            "harbor",
            SettingsEdit(
                expected_revision=settings.revision,
                max_parallel=value,
                selection=settings.selection,
            ),
        )

    def result(task_id):
        page = service.results.page("harbor", task_id)
        return page.items[0] if page.items else None

    async def until(predicate):
        async with asyncio.timeout(20):
            while not predicate():
                await asyncio.sleep(0.05)

    async def exercise():
        await service.start()
        try:
            queue(True)
            await until(lambda: len(workers) == 2)
            runs = execution.page("harbor").items
            assert len(runs) == 2 and {run.status for run in runs} == {"running"}
            assert {run.base_commit for run in runs} == {base}
            assert workers[0].cwd != workers[1].cwd
            assert not (workers[0].cwd / "result-1.txt").exists()
            assert not (workers[1].cwd / "result-0.txt").exists()
            assert baseline(repo) == base
            by_path = {service.location("harbor", run.id).workspace: run for run in runs}
            first, second = [by_path[str(worker.cwd)] for worker in workers]
            assert (
                board_statuses()[first.task_id] == board_statuses()[second.task_id] == "in_progress"
            )

            cap(1)
            assert not any(worker.stopping for worker in workers)
            workers[0].release.set()
            await until(lambda: result(first.task_id) and result(first.task_id).status == "ready")
            await asyncio.sleep(1.1)  # Cross scheduler ticks with one occupied slot at cap one.
            assert len(workers) == 2
            assert execution.get("harbor", second.id).status == "running"
            assert execution.get("harbor", first.id).usage.total_tokens is None
            assert board_statuses()[first.task_id] == "in_review"
            assert board_statuses()[second.task_id] == "in_progress"

            queue(False)
            workers[1].release.set()
            await until(lambda: result(second.task_id) and result(second.task_id).status == "ready")
            await asyncio.sleep(1.1)
            assert len(workers) == 2 and execution.occupancy("harbor").active == 0
            assert execution.get("harbor", second.id).usage.total_tokens is None

            queue(True)
            await until(lambda: len(workers) == 3)
            third = next(run for run in execution.page("harbor").items if run.status == "running")
            queue(False)
            await service.stop("harbor", third.id, RunAction(expected_revision=third.revision))
            await until(lambda: execution.get("harbor", third.id).status == "stopped")
            assert result(first.task_id).status == result(second.task_id).status == "ready"

            # Both approvals bind the same original destination. Only one may deliver unchanged.
            from result_fixtures import approve

            for run in (first, second):
                approve(service, result(run.task_id))
            await until(
                lambda: (
                    {result(run.task_id).status for run in (first, second)}
                    == {"delivered", "stale"}
                )
            )
            stale = next(
                result(run.task_id)
                for run in (first, second)
                if result(run.task_id).status == "stale"
            )
            old_candidate = stale.candidate_commit
            fresh = service.results.reprepare(
                "harbor", stale.id, RunAction(expected_revision=stale.revision)
            )
            await until(lambda: service.results.get("harbor", fresh.id).status == "ready")
            fresh = service.results.get("harbor", fresh.id)
            assert fresh.candidate_commit != old_candidate and fresh.approved_at is None
            assert execution.workspace.task("harbor", fresh.task_id).status == "in_review"
            approve(service, fresh)
            await until(lambda: service.results.get("harbor", fresh.id).status == "delivered")
            assert (repo / "result-0.txt").read_text() == "worker 0\n"
            assert (repo / "result-1.txt").read_text() == "worker 1\n"
            assert not (repo / "result-2.txt").exists()
            assert all(
                execution.workspace.task("harbor", run.task_id).status == "done"
                for run in (first, second)
            )
        finally:
            for worker in workers:
                worker.release.set()
            await service.close()
        resumed = Supervisor(execution.workspace)
        await resumed.start()
        try:
            assert not resumed.execution.settings("harbor").enabled
            assert len(resumed.execution.page("harbor").items) == 3
        finally:
            await resumed.close()

    asyncio.run(exercise())
