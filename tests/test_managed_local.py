"""Production Local/ACP orchestration with real Git, subprocesses and scoped MCP; no models."""

import asyncio
import json
import shlex
import shutil
import sys
from pathlib import Path

import pytest
from test_execution import fixture
from test_results import approve

from flowfield.adapters.git_workspace import baseline, git
from flowfield.agent_models import AgentChoice
from flowfield.application import TaskPublish
from flowfield.execution_models import RunAction, SettingsEdit
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig, InspectionPrepare
from flowfield.integration_models import IntegrationConfig
from flowfield.permission_models import PermissionAnswer
from flowfield.setup_validation import SetupCheckRequest
from flowfield.supervisor import Supervisor

FAKE = Path(__file__).with_name("fake_acp.py")


def configured(tmp_path, monkeypatch, *, count=1, scenario="normal", flags=()):
    execution = fixture(tmp_path, count=count, cap=count)
    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-m", "base")
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", scenario)
    monkeypatch.setenv("CODEX_PATH", sys.executable)  # Native discovery remains model-free in CI.
    monkeypatch.setenv("FLOWFIELD_TEST_SECRET", "do-not-persist-host-secrets")
    monkeypatch.setattr(
        "flowfield.adapters.codex_agent.command",
        lambda directory, env: (
            [sys.executable, str(FAKE), "managed", "cleanup", "close-session", *flags],
            dict(env),
        ),
    )
    service = Supervisor(execution.workspace)
    settings = service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=1,
            target_branch="main",
            runtime="local",
            checks=["test -f base.txt"],
            setup_commands=['test -n "$HOME" && test -n "$FLOWFIELD_RUNTIME_DIR"'],
        ),
    )
    workers = execution.settings("harbor")
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=workers.revision,
            max_parallel=count,
            selection=AgentChoice(model="test-model", effort="low", mode="workspace-write"),
        ),
    )
    for task in execution.workspace.tasks("harbor"):
        execution.workspace.publish_task(
            "harbor",
            task.id,
            TaskPublish(
                completion="code",
                expected_revision=task.revision,
            ),
        )
    return service, repo, settings


def test_local_workers_validate_and_inspect_with_host_tools(tmp_path, monkeypatch):
    service, repo, settings = configured(tmp_path, monkeypatch, count=2)
    base = baseline(repo)

    async def exercise():
        assert (await service.model_options())[0].modes[0].id == "workspace-write"
        checked = await service.setup_validation.check(
            "harbor", SetupCheckRequest(expected_revision=settings.revision)
        )
        assert checked.status == "passed", checked.problem
        runs = [service.execution.claim("harbor", base, {base: set()}) for _ in range(2)]
        assert all(runs)
        await asyncio.gather(*(service._execute(run, repo) for run in runs))
        for run in runs:
            current = service.execution.get("harbor", run.id)
            assert current.status == "in_review", current.problem
            assert (
                current.runtime == "local"
                and current.agent_settings.choice.mode == "workspace-write"
            )
            metadata = service.execution.local(run.id)
            assert metadata["native_cleanup_confirmed"]
            assert "do-not-persist-host-secrets" not in json.dumps(metadata)
            environment = service.environment(run.id)
            assert (environment.checkout / "result.txt").read_text() == run.id
            assert not (environment.runtime / "python").exists()
            assert current.usage.total_tokens is None
        assert runs[0].id != runs[1].id and baseline(repo) == base
        await asyncio.to_thread(service.results.process, "harbor")
        await asyncio.to_thread(service.results.process, "harbor")
        result = service.results.page("harbor", runs[0].task_id).items[0]
        assert result.status == "ready", result.problem
        inspections = Inspections(service.workspace)
        inspections.configure(
            "harbor",
            InspectionConfig(expected_revision=1, run_command='test -n "$FLOWFIELD_RUNTIME_DIR"'),
        )
        copy = await asyncio.to_thread(
            inspections.prepare,
            "harbor",
            InspectionPrepare(result_id=result.id, expected_revision=result.revision),
        )
        assert copy.status == "ready" and copy.runtime == "local"
        # Saved records tolerate unrelated metadata without accepting it as configuration.
        for record in [checked, runs[0], settings, copy]:
            assert (
                type(record).model_validate(
                    {**record.model_dump(), "extra_metadata": {"unused": True}}
                )
                == record
            )
        launcher = Path(copy.launcher).read_text()
        assert "do-not-persist-host-secrets" not in launcher and "export HOME=" not in launcher
        assert service.workspace.task("harbor", runs[0].task_id).status != "done"
        await service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("linked_checkout", [False, True], ids=["checkout", "linked-worktree"])
def test_parallel_mixed_language_project_delivery(tmp_path, monkeypatch, linked_checkout):
    node = shutil.which("node")
    if not node:
        pytest.skip("Mixed-language smoke requires Node.js")
    service, repo, settings = configured(
        tmp_path / "Mixed project 界", monkeypatch, count=2, scenario="monorepo"
    )
    files = {
        "services/api/billing.py": "VALUE = 1\n",
        "apps/web/price.mjs": "export const value = 1;\n",
        "docs/报价 notes.md": "Keep the public contract.\n",
        ".gitignore": ".cache/\ndist/\n__pycache__/\n",
        "scripts/check.py": (
            "import json, runpy, subprocess, sys\n"
            "from pathlib import Path\n"
            "assert Path('.cache/setup').read_text() == str(Path.cwd())\n"
            "api = runpy.run_path('services/api/billing.py')['VALUE']\n"
            "web = json.loads(subprocess.check_output([sys.argv[1], '--input-type=module',\n"
            "    '-e', \"import {value} from './apps/web/price.mjs'; console.log(value)\"]))\n"
            "assert api in (1, 2) and web in (1, 2)\n"
            "assert Path('docs/current').read_text() == 'Keep the public contract.\\n'\n"
            "print(f'Python API {api}; JavaScript UI {web}')\n"
        ),
        "scripts/setup.py": (
            "from pathlib import Path\n"
            "Path('.cache').mkdir(exist_ok=True)\n"
            "Path('.cache/setup').write_text(str(Path.cwd()))\n"
        ),
        "scripts/health": "#!/bin/sh\nprintf 'healthy\\n'\n",
    }
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (repo / "docs/current").symlink_to("报价 notes.md")
    (repo / "scripts/health").chmod(0o755)
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-m", "monorepo")
    if linked_checkout:
        source = repo.with_name("source repository")
        repo.rename(source)
        git(source, "checkout", "--detach")
        git(source, "worktree", "add", str(repo), "main")
    (repo / "dist").mkdir()
    (repo / "dist/human-output.txt").write_text("Leave local artifacts alone")
    python = shlex.quote(sys.executable)
    settings = service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch="main",
            setup_commands=[f"{python} scripts/setup.py"],
            checks=[f"{python} scripts/check.py {shlex.quote(node)}", "./scripts/health"],
        ),
    )
    base = baseline(repo)

    async def exercise():
        try:
            checked = await service.setup_validation.check(
                "harbor", SetupCheckRequest(expected_revision=settings.revision)
            )
            assert checked.status == "passed", checked.problem
            runs = [service.execution.claim("harbor", base, {base: set()}) for _ in range(2)]
            assert all(runs)
            async with asyncio.timeout(45):
                await asyncio.gather(*(service._execute(run, repo) for run in runs))
            for run in runs:
                current = service.execution.get("harbor", run.id)
                assert current.status == "in_review", current.problem
                checkout = service.environment(run.id).checkout
                assert (checkout / ".cache/setup").read_text() == str(checkout)
                assert not (checkout / "dist/human-output.txt").exists()
                changed = git(repo, "diff", "--name-only", base, current.result_commit).decode()
                expected = (
                    "services/api/billing.py" if run.task_id == "task-0" else "apps/web/price.mjs"
                )
                assert changed.strip() == expected
            assert baseline(repo) == base
            assert not (repo / ".cache").exists()
            # Validate and approve against the current destination for each delivery.
            # The second worker started on the same base; its candidate must retain
            # the first delivered change when prepared against the advanced destination.
            for _ in runs:
                await asyncio.to_thread(service.results.process, "harbor")
                versions = [service.results.page("harbor", run.task_id).items[0] for run in runs]
                ready = [version for version in versions if version.status == "ready"]
                assert len(ready) == 1, [(version.status, version.problem) for version in versions]
                version = ready[0]
                approve(service, version)
                await asyncio.to_thread(service.results.process, "harbor")
                version = service.results.page("harbor", version.task_id).items[0]
                assert version.status == "delivered", version.problem
                assert service.workspace.task("harbor", version.task_id).status == "done"
            assert (repo / "services/api/billing.py").read_text() == "VALUE = 2\n"
            assert (repo / "apps/web/price.mjs").read_text() == "export const value = 2;\n"
            assert (repo / "dist/human-output.txt").read_text() == "Leave local artifacts alone"
            assert (repo / "docs/current").is_symlink()
            assert (repo / "scripts/health").stat().st_mode & 0o111
            assert git(repo, "status", "--porcelain") == b""
        finally:
            await service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("long_label", [False, True])
def test_native_permission_is_durable_and_does_not_approve_result(
    tmp_path, monkeypatch, long_label
):
    service, repo, _ = configured(
        tmp_path,
        monkeypatch,
        scenario="permission",
        flags=("long-permission",) if long_label else (),
    )
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})

    async def exercise():
        job = asyncio.create_task(service._execute(run, repo))
        try:
            async with asyncio.timeout(10):
                while not service.permissions.page("harbor").pending:
                    await asyncio.sleep(0.02)
            pending = service.permissions.page("harbor").pending[0]
            assert "command: inspect project" in pending.details
            assert "Before:\nbefore" in pending.details and "After:\nafter" in pending.details
            option = "future" if long_label else "allow"
            if long_label:
                assert len(pending.options[1].label) > 400
                assert pending.options[1].label.endswith('console.log("complete-prefix")\'`')
                assert [o.id for o in pending.options] == ["allow", "future", "deny"]
            service.permissions.answer(
                "harbor",
                pending.id,
                PermissionAnswer(expected_revision=pending.revision, option_id=option),
            )
            await job
            assert service.execution.get("harbor", run.id).status == "in_review"
            saved = service.permissions.page("harbor").items[0]
            assert saved.released_at and saved.answer == option
            assert saved.options == pending.options
            assert service.results.page("harbor", run.task_id).items[0].approved_by is None
            assert baseline(repo) == base
        finally:
            await service.close()
            if not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario,flags", [("disconnect", ()), ("normal", ("cleanup-uncertain",))])
def test_lost_native_cleanup_keeps_capacity_reserved(tmp_path, monkeypatch, scenario, flags):
    service, repo, _ = configured(tmp_path, monkeypatch, scenario=scenario, flags=flags)
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    asyncio.run(service._execute(run, repo))
    current = service.execution.get("harbor", run.id)
    assert current.status == "uncertain", current.problem
    assert not current.result_commit
    service.execution.restart()
    current = service.execution.get("harbor", run.id)
    recovered = asyncio.run(
        service.stop("harbor", run.id, RunAction(expected_revision=current.revision))
    )
    assert recovered.status == "uncertain"
    assert service.execution.occupancy("harbor").uncertain == 1


def test_new_projects_and_attempts_use_local(tmp_path):
    execution = fixture(tmp_path)
    settings = Supervisor(execution.workspace).integrations.settings("harbor")
    assert settings.runtime == "local"
    run = execution.claim("harbor", "a" * 40, {"a" * 40: set()})
    assert run.runtime == "local"


def test_answer_continuation_uses_new_local_attempt_and_saved_code(tmp_path, monkeypatch):
    from flowfield.questions import QuestionAnswer, Questions

    service, repo, _ = configured(tmp_path, monkeypatch, scenario="question")
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    asyncio.run(service._execute(run, repo))
    paused = service.execution.get("harbor", run.id)
    assert paused.status == "waiting_for_input", paused.problem
    questions = Questions(service.workspace)
    question = questions.get("harbor", paused.question_id)
    questions.answer(
        "harbor",
        question.id,
        QuestionAnswer(expected_revision=question.revision, answer="Use the first option"),
    )
    successor = service.execution.claim("harbor", base, {paused.input_checkpoint: set()})
    assert successor.input_base_commit == paused.input_checkpoint
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", "normal")
    asyncio.run(service._execute(successor, repo))
    assert service.execution.get("harbor", successor.id).status == "in_review"
    assert service.environment(successor.id).checkout != service.environment(run.id).checkout
    assert "Use the first option" in service.execution.assignment("harbor", successor.id)["input"]


def test_stop_waiting_permission_revokes_request_and_preserves_work(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch, scenario="permission")
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})

    async def exercise():
        job = asyncio.create_task(service._execute(run, repo))
        service.jobs[run.id] = job
        try:
            async with asyncio.timeout(10):
                while not service.permissions.page("harbor").pending:
                    await asyncio.sleep(0.02)
            current = service.execution.get("harbor", run.id)
            stopped = await service.stop(
                "harbor", run.id, RunAction(expected_revision=current.revision)
            )
            await job
            assert stopped.status == "stopped", stopped.problem
            assert not service.permissions.page("harbor").pending
            assert service.permissions.page("harbor").items[0].status == "cancelled"
            assert (service.environment(run.id).checkout / "result.txt").exists()
            assert service.execution.occupancy("harbor").active == 0
        finally:
            service.jobs.pop(run.id, None)
            await service.close()

    asyncio.run(exercise())


def test_local_commits_capture_original_base_without_moving_shared_branch(tmp_path, monkeypatch):
    from flowfield.adapters.local_execution import LocalHost

    service, repo, _ = configured(tmp_path, monkeypatch)
    base = baseline(repo)
    attempt = LocalHost({}).prepare(service.workspace.directory, repo, "native-commit", base)
    (attempt.checkout / "new.txt").write_text("committed locally")
    git(attempt.checkout, "add", "new.txt")
    git(
        attempt.checkout,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@invalid",
        "commit",
        "-m",
        "local",
    )
    (attempt.checkout / "later.txt").write_text("uncommitted")
    result, _ = attempt.snapshot(base)
    assert git(repo, "rev-parse", result + "^").decode().strip() == base
    assert git(repo, "show", result + ":new.txt") == b"committed locally"
    assert git(repo, "show", result + ":later.txt") == b"uncommitted"
    assert baseline(repo) == base


def test_public_permission_projection_keeps_commands_and_diff_but_not_private_inputs():
    from acp.schema import ToolCallUpdate

    from flowfield.adapters.codex_agent import codex_permission_details

    tool = ToolCallUpdate.model_validate(
        {
            "toolCallId": "edit",
            "title": "Edit files",
            "locations": [{"path": "/project/a"}],
            "content": [
                {"type": "diff", "path": "/project/a", "oldText": "before", "newText": "after"}
            ],
            "rawInput": {
                "command": "pnpm test",
                "cwd": "/project",
                "authorization": "private-token",
            },
            "_meta": {"private": "private-metadata"},
        }
    )
    detail = codex_permission_details(tool)
    assert all(text in detail for text in ("pnpm test", "/project/a", "before", "after"))
    assert "private-token" not in detail and "private-metadata" not in detail
    tool.raw_input = {"command": "x" * 20000}
    assert len(codex_permission_details(tool)) <= 16000
    assert codex_permission_details(tool).endswith("[Details truncated]")


def test_recovery_never_signals_a_saved_local_pid(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch)
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    service.execution.save_local(
        run.id,
        {
            "runtime_kind": "local",
            "native_launch_started": True,
            "pid": 12345,
            "process_stamp": "same-time",
        },
    )
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "same-time")

    def unexpected_signal(*args):
        raise AssertionError("A saved PID is not a live owner")

    monkeypatch.setattr("flowfield.supervisor.os.killpg", unexpected_signal)
    service.execution.restart()
    current = service.execution.get("harbor", run.id)
    stopped = asyncio.run(
        service.stop("harbor", run.id, RunAction(expected_revision=current.revision))
    )
    assert stopped.status == "uncertain"


def test_stop_during_local_setup_stops_owned_command_before_freeing_slot(tmp_path, monkeypatch):
    service, repo, settings = configured(tmp_path, monkeypatch)
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch="main",
            checks=["true"],
            setup_commands=["while :; do sleep 1; done"],
            setup_timeout_seconds=60,
        ),
    )
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})

    async def exercise():
        job = asyncio.create_task(service._execute(run, repo))
        service.jobs[run.id] = job
        try:
            async with asyncio.timeout(10):
                while not service.execution.local(run.id).get("setup_process"):
                    await asyncio.sleep(0.02)
            current = service.execution.get("harbor", run.id)
            stopped = await service.stop(
                "harbor", run.id, RunAction(expected_revision=current.revision)
            )
            await job
            assert stopped.status == "stopped", stopped.problem
            assert service.execution.local(run.id)["setup_process"] is None
            assert stopped.started_at is None and stopped.result_commit is None
        finally:
            service.jobs.pop(run.id, None)
            await service.close()

    asyncio.run(exercise())


def test_native_mode_discovery_needs_no_environment_activation(tmp_path, monkeypatch):
    from flowfield.agent_models import AgentChoice
    from flowfield.execution_models import ModelOption

    service = Supervisor(fixture(tmp_path).workspace)

    async def catalog(*, project_id, harness):
        assert project_id == "harbor" and harness == "codex"
        return [ModelOption(id="test-model", name="Test", efforts=["low"], modes=[])]

    monkeypatch.setattr(service, "model_options", catalog)
    asyncio.run(
        service.validate_agent_choice(AgentChoice(model="test-model", effort="low"), "harbor")
    )


def test_local_setup_failure_explains_host_tools_not_retired_inventory(tmp_path, monkeypatch):
    service, repo, settings = configured(tmp_path, monkeypatch)
    settings = service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch="main",
            checks=["flowfield_nonexistent_test_tool_1736254"],
        ),
    )
    value = asyncio.run(
        service.setup_validation.check(
            "harbor", SetupCheckRequest(expected_revision=settings.revision)
        )
    )
    assert value.status == "failed" and "service host" in value.problem
    assert "Environment tools" not in value.problem
    assert baseline(repo) == value.commit


def test_stopping_one_parallel_acp_worker_keeps_the_other_permission_live(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch, count=2, scenario="permission")
    base = baseline(repo)
    runs = [service.execution.claim("harbor", base, {base: set()}) for _ in range(2)]

    async def exercise():
        jobs = [asyncio.create_task(service._execute(run, repo)) for run in runs]
        service.jobs.update({run.id: job for run, job in zip(runs, jobs, strict=True)})
        try:
            async with asyncio.timeout(10):
                while len(service.permissions.page("harbor").pending) < 2:
                    await asyncio.sleep(0.02)
            first = service.execution.get("harbor", runs[0].id)
            await service.stop("harbor", first.id, RunAction(expected_revision=first.revision))
            pending = service.permissions.page("harbor").pending
            assert len(pending) == 1 and pending[0].run_id == runs[1].id
            assert not jobs[1].done()
            service.permissions.answer(
                "harbor",
                pending[0].id,
                PermissionAnswer(expected_revision=pending[0].revision, option_id="allow"),
            )
            await asyncio.gather(*jobs)
            assert service.execution.get("harbor", runs[0].id).status == "stopped"
            assert service.execution.get("harbor", runs[1].id).status == "in_review"
        finally:
            await service.close()
            service.jobs.clear()

    asyncio.run(exercise())
