"""One reporting reminder shares the attempt, permissions and exact review boundary."""

import asyncio
import json

import pytest
from execution_fixtures import fixture
from fake_worker import FakeWorker

from flowfield.adapters.git_workspace import baseline, git
from flowfield.application import TaskPublish
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.integration_models import IntegrationConfig
from flowfield.run_activity import RunActivity
from flowfield.supervisor import Supervisor


@pytest.mark.parametrize(
    "behavior,expected,prompts",
    [
        ("recover", "in_review", 2),
        ("partial", "in_review", 2),
        ("ask", "waiting_for_input", 2),
        ("silent", "failed", 2),
        ("slow_reporting", "in_review", 2),
        ("uncertain", "uncertain", 2),
        ("stop_during", "stopped", 2),
        ("cancel_during", "stopped", 2),
        ("submitted", "in_review", 1),
        ("question", "waiting_for_input", 1),
        ("interrupted", "failed", 1),
        ("error", "failed", 1),
        ("stopping", "stopped", 1),
        ("closing", "failed", 1),
    ],
)
def test_single_reporting_reminder(tmp_path, monkeypatch, behavior, expected, prompts):
    workers = []
    execution = fixture(tmp_path)
    repository = tmp_path / "harbor"
    git(repository, "init", "-b", "main")
    (repository / "base.txt").write_text("base")
    git(repository, "add", ".")
    git(
        repository,
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
        IntegrationConfig(expected_revision=1, target_branch="main", checks=["test -f result.txt"]),
    )
    task = execution.workspace.task("harbor", "task-0")
    execution.workspace.publish_task(
        "harbor", task.id, TaskPublish(expected_revision=task.revision, completion="code")
    )
    head = baseline(repository)
    run = execution.claim("harbor", head, {head: set()})
    assert run

    class Worker(FakeWorker):
        async def prompt(self, text, on_permission, *, attachments=None):
            workers.append(self)
            count = len(workers)
            assert not self.stopping and on_permission is not None
            assert execution.get("harbor", run.id).status == "running"
            if count == 1:
                assert json.loads(text)["task_key"] == run.task_key
                (self.cwd / "result.txt").write_text("preserved first-turn work")
                if behavior == "interrupted":
                    return {"status": "interrupted"}
                if behavior == "error":
                    raise ApplicationError("fixture_error", "Native fixture failed")
                if behavior == "stopping":
                    execution.stop_requested(
                        "harbor",
                        run.id,
                        RunAction(expected_revision=execution.get("harbor", run.id).revision),
                    )
                if behavior == "closing":
                    service.closing = True
                if behavior not in {"submitted", "question"}:
                    return {"status": "completed"}
            else:
                assert count == 2 and workers[0] is self
                assert self.session_id == "fixture-" + self.cwd.parent.name
                assert (self.cwd / "result.txt").read_text() == "preserved first-turn work"
                assert not attachments
                if behavior == "slow_reporting":
                    # Advance past the former deadline without a real minute-long wait.
                    loop = asyncio.get_running_loop()
                    clock = loop.time
                    monkeypatch.setattr(loop, "time", lambda: clock() + 61)
                    await asyncio.sleep(0.001)
                if behavior == "stop_during":
                    # Same public stop operation while the reminder is running.
                    self.stop_job = asyncio.create_task(
                        service.stop(
                            "harbor",
                            run.id,
                            RunAction(expected_revision=execution.get("harbor", run.id).revision),
                        )
                    )
                    while not self.stopping:
                        await asyncio.sleep(0)
                    return {"status": "stopped"}
                if behavior == "cancel_during":
                    asyncio.current_task().cancel()
                    await asyncio.sleep(0)
                if behavior in {"silent", "uncertain"}:
                    return {"status": "completed"}
            if behavior in {"ask", "question"}:
                await self.on_tool(
                    "ask_question",
                    {
                        "question": "Which target is intended?",
                        "context": "Target is ambiguous.",
                        "recommendation": "Confirm the intended target before continuing.",
                    },
                )
            else:
                await self.on_tool(
                    "submit_result",
                    {
                        "outcome": "partial" if behavior == "partial" else "complete",
                        "summary": "Actual work",
                        "checks": "Fixture evidence",
                        "remaining_work": "Finish the remaining behavior"
                        if behavior == "partial"
                        else "",
                    },
                )
            return {"status": "completed"}

        async def stop(self):
            self.stopping = True
            self.cleanup_confirmed = behavior != "uncertain"
            return self.cleanup_confirmed

    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", Worker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture")

    async def exercise():
        job = asyncio.create_task(service._execute(run, repository))
        service.jobs[run.id] = job
        await job
        if behavior == "stop_during":
            await workers[0].stop_job

    asyncio.run(exercise())
    current = execution.get("harbor", run.id)
    assert current.status == expected
    assert len(workers) == prompts
    assert baseline(repository) == head  # Recovery never approves or delivers code.
    assert len(execution.page("harbor").items) == 1
    assert (workers[0].cwd / "result.txt").read_text() == "preserved first-turn work"
    activity = RunActivity(execution.workspace).read("harbor", run.id)
    reminders = [item for item in activity.items if "Asking once" in item.text]
    assert len(reminders) == (prompts == 2)
    if behavior == "partial":
        assert current.result.outcome == "partial" and current.result.remaining_work
    if behavior in {"ask", "question"}:
        assert current.question_id and current.input_checkpoint
    if expected in {"failed", "stopped", "uncertain"}:
        assert not current.result_commit and not current.result
