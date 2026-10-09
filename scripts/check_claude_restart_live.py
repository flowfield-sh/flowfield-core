"""Explicit native in-flight service-loss proof with isolated retained state.

Kills only the live LocalProcess owner of this fixture service. Saved bridge/helper
PIDs are observations, never termination authority. Lost native cleanup must reserve
capacity even when the bounded helper expires. No personal service or model replay.
"""

import argparse
import asyncio
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from flowfield.adapters.agent_selection import create
from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_process import LocalProcess
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.application import ProjectSetup, TaskCreate, TaskPublish, Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, RunAction, SettingsEdit
from flowfield.harness_models import HarnessEdit
from flowfield.harness_settings import HarnessSettings
from flowfield.integration_models import IntegrationConfig
from flowfield.permission_models import PermissionAnswer
from flowfield.stage_models import Stage
from flowfield.supervisor import Supervisor

MODEL = "claude-sonnet-5-5"
CHOICE = AgentChoice(harness="claude-code", model=MODEL, effort="low", mode="default", fast=False)


async def child(trial: Path, bridge: Path, native: Path, role: str) -> None:
    repo = trial / "project"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(
        "Run only the explicit bounded fixture helper. Preserve files.\n"
    )
    workspace = Workspace(trial / "state")
    project = workspace.setup_project(ProjectSetup(path=str(repo), id="restart-proof"))
    git(repo, "init", "-b", "main")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@invalid",
        "commit",
        "-m",
        "Fixture",
    )
    helper = trial / "bounded_helper.py"
    helper.write_text(
        "import json, os, time\nfrom pathlib import Path\n"
        f"Path({str(trial / 'ready.json')!r}).write_text(json.dumps("
        f"{{'pid':os.getpid(),'nonce':{trial.name!r}}}))\n"
        "time.sleep(75)\n"
    )
    command = shlex.quote(sys.executable) + " " + shlex.quote(str(helper))
    instruction = (
        "Run this exact bounded fixture command once using native Bash in the foreground: "
        + command
        + ". It waits deliberately for a service-loss proof. Do not background it, edit files, "
        "ask questions, submit results, or run any other command. Wait for it."
    )
    service = Supervisor(workspace)
    HarnessSettings(workspace).edit(
        "claude-code", HarnessEdit(expected_revision=1, executable=str(native))
    )
    launches = []

    def managed(choice, directory, cwd, environment, *, registration=None):
        assert choice.model == MODEL and choice.harness == "claude-code"
        agent = create(
            choice,
            directory,
            cwd,
            environment,
            registration=registration,
            claude_proof_bridge=bridge,
        )
        original = agent.prompt

        async def observed_prompt(*args, **kwargs):
            await agent._verify_model()
            launches.append({"observedModel": agent.native_model})
            (trial / "launches.json").write_text(json.dumps(launches))
            return await original(*args, **kwargs)

        agent.prompt = observed_prompt
        return agent

    with (
        patch("flowfield.coordinator.create", managed),
        patch("flowfield.supervisor.create", managed),
    ):
        try:
            await service.start()
            if role == "coordinator":
                AgentSettings(workspace).edit(
                    project.id, role, AgentSettingsEdit(expected_revision=1, selection=CHOICE)
                )
                conversation = service.coordinator.store.new(project.id)
                turn = service.coordinator.send(
                    project.id, conversation.id, CoordinatorSend(id=uuid4().hex, text=instruction)
                )
                identity = turn.id
                job = service.coordinator.jobs[identity]
            else:
                service.integrations.configure(
                    project.id,
                    IntegrationConfig(
                        expected_revision=1,
                        target_branch="main",
                        runtime="local",
                        checks=["test -f CLAUDE.md"],
                    ),
                )
                settings = service.execution.configure(
                    project.id,
                    SettingsEdit(expected_revision=1, model=MODEL, effort="low", mode="default"),
                )
                task = workspace.create_task(
                    project.id,
                    TaskCreate(
                        id="control",
                        title="Bounded native control proof",
                        body=instruction,
                        status="up_next",
                        stages=[
                            Stage(
                                id="control",
                                title="Control",
                                outcome="Run the bounded fixture helper",
                            )
                        ],
                    ),
                )
                task = workspace.publish_task(
                    project.id,
                    task.id,
                    TaskPublish(expected_revision=task.revision, completion="report"),
                )
                AgentSettings(workspace).edit(
                    project.id,
                    role,
                    AgentSettingsEdit(expected_revision=1, selection=CHOICE),
                    task.id,
                )
                settings = service.execution.queue(
                    project.id, QueueEdit(expected_revision=settings.revision, enabled=True)
                )
                head = baseline(repo)
                run = service.execution.claim(project.id, head, {head: set()})
                assert run is not None
                # Pause synchronously before the scheduler can start another attempt.
                service.execution.queue(
                    project.id, QueueEdit(expected_revision=settings.revision, enabled=False)
                )
                identity = run.id
                job = asyncio.create_task(service._execute(run, repo))
                service.jobs[identity] = job
            (trial / "identity.json").write_text(json.dumps({"id": identity, "role": role}))
            while not job.done():
                for request in service.permissions.page(project.id).pending:
                    once = next(option for option in request.options if option.kind == "allow_once")
                    service.permissions.answer(
                        project.id,
                        request.id,
                        PermissionAnswer(expected_revision=request.revision, option_id=once.id),
                    )
                await asyncio.sleep(0.05)
            await job
            raise RuntimeError("Fixture completed before the parent interrupted it")
        finally:
            await service.close()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # Observation only, never cleanup authority.
    except ProcessLookupError:
        return False
    return True


async def parent(trial: Path, bridge: Path, native: Path, role: str) -> dict:
    artifact = trial / "bridge"
    shutil.copy2(bridge, artifact)
    owner = await LocalProcess.start(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            str(artifact),
            "--native",
            str(native),
            "--trial-root",
            str(trial),
            "--role",
            role,
            "--invoke-live",
            "--child",
        ],
        cwd=trial,
        env=os.environ,
    )
    assert owner.process.stdout and owner.process.stderr

    async def discard(stream):
        while await stream.read(8192):
            pass

    readers = [
        asyncio.create_task(discard(owner.process.stdout)),
        asyncio.create_task(discard(owner.process.stderr)),
    ]
    report = {"role": role, "requestedModel": MODEL, "passed": False}
    restored = None
    try:
        async with asyncio.timeout(90):
            while not (trial / "ready.json").exists():
                if owner.process.returncode is not None:
                    raise RuntimeError("Fixture service exited before native helper startup")
                await asyncio.sleep(0.05)
        ready = json.loads((trial / "ready.json").read_text())
        assert ready["nonce"] == trial.name and type(ready["pid"]) is int and ready["pid"] > 1
        assert alive(ready["pid"])
        launches = json.loads((trial / "launches.json").read_text())
        assert launches == [{"observedModel": MODEL}]
        identity = json.loads((trial / "identity.json").read_text())["id"]
        report.update(observedModel=MODEL, helperObservedLive=True)
        print(json.dumps({"nativeHelperLive": True, "role": role, "trial": str(trial)}), flush=True)
        # Abrupt service-process loss. Its detached native bridge/query do not become
        # authority of this owner merely because their IDs exist in application state.
        assert await owner.close(timeout=2)
        restored = Supervisor(Workspace(trial / "state"))

        def forbidden(*args, **kwargs):
            raise AssertionError("Restart must not replay an uncertain native prompt")

        with (
            patch("flowfield.coordinator.create", forbidden),
            patch("flowfield.supervisor.create", forbidden),
        ):
            await restored.start()
            if role == "coordinator":
                turn = restored.coordinator.store.get("restart-proof", identity)
                assert turn.status == "uncertain"
                page = restored.coordinator.store.page("restart-proof")
                assert page.active and page.active.id == identity
                try:
                    restored.coordinator.send(
                        "restart-proof",
                        turn.conversation_id,
                        CoordinatorSend(id=uuid4().hex, text="Must not dispatch"),
                    )
                except ApplicationError as error:
                    assert error.code == "coordinator_busy"
                else:
                    raise AssertionError("Uncertain conversation did not block dispatch")
            else:
                run = restored.execution.get("restart-proof", identity)
                assert run.status == "uncertain"
                run = await restored.stop(
                    "restart-proof", identity, RunAction(expected_revision=run.revision)
                )
                assert run.status == "uncertain"
                assert restored.execution.occupancy("restart-proof").uncertain == 1
                assert (
                    restored.execution.local(identity).get("native_cleanup_confirmed") is not True
                )
            assert json.loads((trial / "launches.json").read_text()) == launches
            report.update(
                recoveredStatus="uncertain", promptReplayed=False, nativeCleanupConfirmed=False
            )
            # Native expiry is independent observational evidence, not a cleanup receipt.
            async with asyncio.timeout(80):
                while alive(ready["pid"]):
                    await asyncio.sleep(0.1)
            report.update(helperObservedGone=True, passed=True)
    except Exception as error:
        report["failure"] = type(error).__name__
    finally:
        if restored:
            await restored.close()
        await owner.close(timeout=2)
        await asyncio.gather(*readers)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bridge", type=Path)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--trial-root", type=Path, required=True)
    parser.add_argument("--role", choices=["coordinator", "worker"], required=True)
    parser.add_argument("--invoke-live", action="store_true", required=True)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.trial_root.resolve(strict=True)
    source = Path(__file__).resolve().parents[1]
    if source.parent in (root, *root.parents) or any(
        path in (root, *root.parents)
        for path in (Path("/tmp").resolve(), Path("/var/folders").resolve())
    ):
        parser.error("Use a dedicated trial root outside source and shared temporary directories")
    bridge, native = args.bridge.resolve(strict=True), args.native.resolve(strict=True)
    if args.child:
        asyncio.run(child(root, bridge, native, args.role))
        return
    trial = root / f"h1-restart-{args.role}-{uuid4().hex}"
    trial.mkdir(mode=0o700)
    report = asyncio.run(parent(trial, bridge, native, args.role))
    (trial / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"trial": str(trial), **report}, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
