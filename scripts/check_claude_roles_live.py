"""Explicit internal Claude role proof, outside ordinary tests and personal services.

Root supplies simulated human intent/permissions in an independent Git fixture. The
actual Coordinator/Supervisor own native launches, scoped tools, records and Stop.
This is capability evidence, not the complete journey or human milestone acceptance.
"""

import argparse
import asyncio
import json
import shutil
import traceback
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from flowfield.adapters.agent_selection import create
from flowfield.adapters.git_workspace import baseline, git
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.agent_tools import ScopedTools
from flowfield.application import ProjectSetup, TaskCreate, TaskPublish, Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.execution_models import QueueEdit, SettingsEdit
from flowfield.harness_models import HarnessEdit
from flowfield.harness_settings import HarnessSettings
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionPrepare
from flowfield.integration_models import IntegrationConfig
from flowfield.permission_models import PermissionAnswer
from flowfield.questions import QuestionAnswer, Questions
from flowfield.result_models import ResultReview
from flowfield.stage_models import Stage
from flowfield.supervisor import Supervisor

MODEL = "claude-sonnet-5-5"
CHOICE = AgentChoice(harness="claude-code", model=MODEL, effort="low", mode="default", fast=False)
CODEX_CHOICE = AgentChoice(
    harness="codex", model="gpt-6.1-sol", effort="medium", mode="read-only", fast=False
)


async def continuity(service, project, conversation, nonce, calls, observed, settle, report):
    """Simulated public prehistory, then five actual turns owned by the service."""
    store = service.coordinator.store
    note = (
        "Unresolved shipping-label requirement. This long saved note has background, then "
        "the exact requirement, then more background.\n"
        + "Background remains unchanged.\n" * 100
        + f"\nThe unresolved shipping label is {nonce}. Preserve it exactly.\n"
        + "Additional background remains unchanged.\n" * 100
    )
    for text in [
        note,
        *(f"Later saved discussion {i}: no shipping-label decision." for i in range(9)),
    ]:
        turn, _ = store.reserve(
            project, conversation.id, CoordinatorSend(id=uuid4().hex, text=text)
        )
        with service.workspace.connection(write=True) as db:
            turn.status = "interrupted"
            turn.notice = "Simulated saved human message; no native prompt was dispatched."
            store._save(db, turn)

    previous = []
    report["turns"] = []
    scenarios = [
        (
            CHOICE,
            "Recover the unresolved shipping label from our older saved conversation and "
            "repeat it exactly. The later discussions did not resolve it. Use Flowfield's "
            "saved context as needed. Do not edit anything.",
            "new",
        ),
        (
            CHOICE,
            "Repeat the unresolved shipping label from our conversation. Do not edit.",
            "resumed",
        ),
        (
            CODEX_CHOICE,
            "We switched harnesses. Repeat the unresolved shipping label from our saved "
            "conversation and name any unclear context/tool steps. Do not edit anything.",
            "new",
        ),
        (
            CHOICE,
            "We switched back. Repeat the unresolved shipping label, preserving the "
            "intervening discussion. Do not edit anything.",
            "new",
        ),
        (
            CHOICE.model_copy(update={"mode": "acceptEdits"}),
            "Write shipping-label.txt containing exactly the unresolved shipping label "
            "followed by a newline. Use the label from our saved conversation. Do not edit "
            "other files or commit. Report the label and any unclear context/tool steps.",
            "new",
        ),
    ]
    for index, (choice, intent, session) in enumerate(scenarios):
        settings = AgentSettings(service.workspace)
        current = settings.get(project, "coordinator")
        if current.selection != choice:
            settings.edit(
                project,
                "coordinator",
                AgentSettingsEdit(expected_revision=current.revision, selection=choice),
            )
        before = len(calls)
        turn = service.coordinator.send(
            project, conversation.id, CoordinatorSend(id=uuid4().hex, text=intent)
        )
        print(json.dumps({"started": "continuity", "step": index + 1}), flush=True)
        await settle(service.coordinator.jobs[turn.id])
        outcome = store.get(project, turn.id)
        public = "\n".join(item.text for item in outcome.activity.items if item.kind == "agent")
        report["turns"].append(
            {
                "step": index + 1,
                "status": outcome.status,
                "session": outcome.session,
                "requested": choice.model_dump(),
                "applied": outcome.applied.choice.model_dump() if outcome.applied else None,
                "labelRecalled": nonce in public,
                "scopedCalls": calls[before:],
                "cleanupConfirmed": observed[-1].cleanup_confirmed,
            }
        )
        assert outcome.status == "completed", outcome.notice
        assert outcome.session == session and outcome.applied.choice == choice
        assert nonce in public and observed[-1].cleanup_confirmed
        with service.workspace.connection() as db:
            current_generation, fresh = store.sessions.current(db, project)
            assert not fresh and current_generation.session_id == observed[-1].session.session_id
            if index == 1:
                assert current_generation.id == previous[-1].id
            elif previous:
                assert current_generation.id != previous[-1].id
                assert current_generation.source_id == previous[-1].id
            for older in previous:
                assert store.sessions.get(db, project, older.id).session_id == older.session_id
            previous.append(current_generation)
        if index == 0:
            assert {"get_coordinator_history", "get_text"} <= {v["tool"] for v in calls[before:]}
    assert (
        Path(service.workspace.project(project).path) / "shipping-label.txt"
    ).read_text() == nonce + "\n"
    report["retainedGenerations"] = len({v.id for v in previous})
    report["simulatedHistoricalMessages"] = 10


async def worker_journey(
    service, project, task, paused, repo, initial, permissions, observed, report
):
    """Simulated human input/review through ordinary owners; scheduling continues once."""

    async def wait(read):
        async with asyncio.timeout(180):
            while True:
                for request in service.permissions.page(project).pending:
                    option = next((v for v in request.options if v.kind == "allow_once"), None)
                    assert option is not None
                    service.permissions.answer(
                        project,
                        request.id,
                        PermissionAnswer(
                            expected_revision=request.revision,
                            option_id=option.id,
                        ),
                    )
                    permissions.append({"role": request.role, "kind": option.kind})
                runs = service.execution.page(project, task_id=task).items
                assert len(runs) <= 3, "Trial exceeded three worker attempts"
                latest = runs[0]
                if latest.status in {"failed", "uncertain", "stopped"}:
                    report["workerFailure"] = {"status": latest.status, "problem": latest.problem}
                    raise AssertionError("Worker journey stopped")
                value = read()
                if value is not None:
                    return value
                await asyncio.sleep(0.05)

    async def result_ready():
        def read():
            page = service.results.page(project, task)
            if not page.items:
                return None
            current = page.items[0]
            if current.status in {"blocked", "stale", "cancelled"}:
                report["resultFailure"] = {"status": current.status, "problem": current.problem}
                raise AssertionError("Result preparation failed")
            return current if current.status == "ready" else None

        return await wait(read)

    report["phase"] = "answer"
    questions = Questions(service.workspace)
    question = questions.get(project, paused.question_id)
    assert question.status == "open" and not (repo / "label.txt").exists()
    assert service.loop_task is not None and service.execution.settings(project).enabled
    answer = QuestionAnswer(expected_revision=question.revision, answer="Use Teal.")
    saved = questions.answer(project, question.id, answer)
    assert saved.answer == "Use Teal."
    assert questions.answer(project, question.id, answer).answer == "Use Teal."
    first = await result_ready()
    continued = service.execution.get(project, first.run_id)
    assert continued.id != paused.id and continued.input_base_commit == paused.input_checkpoint
    assert continued.applied_agent == CHOICE and observed[-1].cleanup_confirmed
    report["answerDeliveredOnce"] = (
        questions.get(project, question.id).continuation_run_id == continued.id
    )
    assert report["answerDeliveredOnce"]
    assert len(service.execution.page(project, task_id=task).items) == 2
    assert baseline(repo) == initial and not (repo / "label.txt").exists()
    assert first.approved_at is None and service.workspace.task(project, task).status == "in_review"
    report["phase"] = "inspect"
    copy = await asyncio.to_thread(
        Inspections(service.workspace).prepare,
        project,
        InspectionPrepare(result_id=first.id, expected_revision=first.revision),
    )
    assert copy.status == "ready" and copy.commit == first.candidate_commit
    assert (Path(copy.workspace) / "label.txt").read_text() == "teal\n"
    assert baseline(repo) == initial and first.approved_at is None
    report["inspectionPassed"] = True
    report["firstLimitations"] = first.report.limitations
    report["phase"] = "request_changes"
    first = service.results.get(project, first.id)
    service.results.review(
        project,
        first.id,
        ResultReview(
            expected_revision=first.revision,
            candidate_commit=first.candidate_commit,
            action="request_changes",
            note="Use uppercase for the same chosen color in label.txt. "
            "Keep the newline and all other files unchanged.",
            author="simulated-human",
        ),
    )
    revised = await result_ready()
    assert revised.id != first.id and revised.run_id != first.run_id
    successor = service.execution.get(project, revised.run_id)
    assert successor.base_commit == first.source_commit
    assert successor.applied_agent == CHOICE and observed[-1].cleanup_confirmed
    assert git(repo, "show", revised.candidate_commit + ":label.txt") == b"TEAL\n"
    assert baseline(repo) == initial and not (repo / "label.txt").exists()
    assert revised.approved_at is None
    report["phase"] = "approve_exact_candidate"
    approved = service.results.review(
        project,
        revised.id,
        ResultReview(
            expected_revision=revised.revision,
            candidate_commit=revised.candidate_commit,
            action="approve",
            note="Simulated human verified the exact uppercase label candidate.",
            author="simulated-human",
        ),
    )
    assert approved.status == "delivering"
    delivered = await wait(
        lambda: (
            service.results.get(project, revised.id)
            if service.results.get(project, revised.id).status == "delivered"
            else None
        )
    )
    assert delivered.approved_by == "simulated-human"
    assert baseline(repo) == revised.candidate_commit
    assert (repo / "label.txt").read_text() == "TEAL\n"
    assert service.workspace.task(project, task).status == "done"
    assert len(observed) == 3 and all(v.cleanup_confirmed for v in observed)
    report.update(
        status="delivered",
        phase="delivered",
        workerAttempts=3,
        requestedChangesApplied=True,
        exactApproval=True,
        deliveryPassed=True,
        finalLimitations=revised.report.limitations,
        queryLimits="Ordinary native worker settings; three attempts, 180s phase bounds",
    )


async def proof(
    trial: Path,
    bridge: Path,
    native: Path,
    role: str,
    *,
    bundle: bool = False,
    codex_bundle: Path | None = None,
    codex_native: Path | None = None,
) -> dict:
    repo = trial / "project"
    repo.mkdir()
    nonce = uuid4().hex
    guidance = f"Fixture guidance word: {nonce}. Preserve this fixture and local instructions.\n"
    (repo / "CLAUDE.md").write_text(guidance)
    (repo / "AGENTS.md").write_text("This independent fixture requires human input before edits.\n")
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
    executable = trial / "bridge"
    if not bundle:
        shutil.copy2(bridge, executable)
    workspace = Workspace(trial / "state")
    if bundle:
        from flowfield.adapters import claude_install

        executable = claude_install.install(
            workspace.directory, bridge, claude_install.digest(bridge)
        )
    project = workspace.setup_project(ProjectSetup(path=str(repo), id="native-proof"))
    # Fixture-owned preparation includes the adopted identity, before native work.
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Adopted fixture",
    )
    initial = baseline(repo)
    service = Supervisor(workspace)
    HarnessSettings(workspace).edit(
        "claude-code", HarnessEdit(expected_revision=1, executable=str(native))
    )
    if role == "continuity":
        from flowfield.adapters import codex_install

        assert bundle and codex_bundle and codex_native
        codex_install.install(workspace.directory, codex_bundle, codex_install.digest(codex_bundle))
        HarnessSettings(workspace).edit(
            "codex", HarnessEdit(expected_revision=1, executable=str(codex_native))
        )
    observed = []
    calls = []
    permissions = []
    original_call = ScopedTools.call

    async def recorded_call(grant, name, arguments):
        result = await original_call(grant, name, arguments)
        calls.append({"tool": name, "failed": bool(result.isError)})
        return result

    def managed(choice, directory, cwd, environment, *, registration=None):
        assert choice == CODEX_CHOICE or choice.harness == "claude-code" and choice.model == MODEL
        if choice.harness == "codex":
            agent = create(choice, directory, cwd, environment, registration=registration)
        elif bundle:
            from flowfield.adapters.claude_agent import ClaudeAgent

            agent = ClaudeAgent(
                cwd,
                environment,
                directory=directory,
                registration=registration,
                choice=choice,
                proof_limits=role != "journey",
            )
        else:
            agent = create(
                choice,
                directory,
                cwd,
                environment,
                registration=registration,
                claude_proof_bridge=executable,
            )
        observed.append(agent)
        return agent

    async def settle(job):
        async with asyncio.timeout(120):
            while not job.done():
                for request in service.permissions.page(project.id).pending:
                    # Simulated human/native tool authorization only; never code approval.
                    option = next((v for v in request.options if v.kind == "allow_once"), None)
                    if option is None:
                        raise RuntimeError("No bounded native permission option")
                    service.permissions.answer(
                        project.id,
                        request.id,
                        PermissionAnswer(
                            expected_revision=request.revision,
                            option_id=option.id,
                        ),
                    )
                    permissions.append({"role": request.role, "kind": option.kind})
                await asyncio.sleep(0.05)
            await job

    report = {
        "role": role,
        "requestedModel": MODEL,
        "effort": "low",
        "passed": False,
        "installedRuntime": bundle,
    }
    try:
        with (
            patch("flowfield.coordinator.create", managed),
            patch("flowfield.supervisor.create", managed),
            patch.object(ScopedTools, "call", recorded_call),
        ):
            if role in {"coordinator", "continuity"}:
                AgentSettings(workspace).edit(
                    project.id,
                    "coordinator",
                    AgentSettingsEdit(
                        expected_revision=1,
                        selection=CHOICE,
                    ),
                )
                conversation = service.coordinator.store.new(project.id)
            if role == "continuity":
                await continuity(
                    service,
                    project.id,
                    conversation,
                    uuid4().hex,
                    calls,
                    observed,
                    settle,
                    report,
                )
            elif role == "coordinator":
                turn = service.coordinator.send(
                    project.id,
                    conversation.id,
                    CoordinatorSend(
                        id=uuid4().hex,
                        text="Read this project's scoped project and board. "
                        "Read CLAUDE.md and AGENTS.md. "
                        "Report the fixture guidance word and whether any tasks exist. Do not edit "
                        "files, tasks or settings, or use ambient Flowfield connections.",
                    ),
                )
                print(json.dumps({"started": role, "trial": str(trial)}), flush=True)
                await settle(service.coordinator.jobs[turn.id])
                outcome = service.coordinator.store.get(project.id, turn.id)
                text = "\n".join(
                    item.text for item in outcome.activity.items if item.kind == "agent"
                )
                report.update(status=outcome.status, guidanceRecalled=nonce in text)
                assert outcome.status == "completed", outcome.notice
                assert outcome.applied.choice == CHOICE
                assert nonce in text
                assert {"get_project", "get_board"} <= {item["tool"] for item in calls}
            else:
                service.integrations.configure(
                    project.id,
                    IntegrationConfig(
                        expected_revision=1,
                        target_branch="main",
                        runtime="local",
                        checks=["test -f label.txt"]
                        if role == "journey"
                        else ["test -f CLAUDE.md"],
                    ),
                )
                configured = service.execution.configure(
                    project.id,
                    SettingsEdit(expected_revision=1, selection=CHOICE),
                )
                if role == "journey":
                    # Startup intentionally pauses queues. Enable the fresh fixture only
                    # after startup, before its first attempt and the single saved answer.
                    await service.start()
                    configured = service.execution.settings(project.id)
                service.execution.queue(
                    project.id,
                    QueueEdit(
                        expected_revision=configured.revision,
                        enabled=True,
                    ),
                )
                task = workspace.create_task(
                    project.id,
                    TaskCreate(
                        id="label",
                        title="Clarify the fixture label",
                        status="up_next",
                        body=(
                            "Create label.txt after the human chooses Amber or Teal. Ask for that "
                            "choice before editing; when a saved answer exists, continue with it "
                            "without asking again. Write the chosen lowercase color followed by "
                            "a newline, verify the file and preserve repository guidance. Only "
                            "edit label.txt. Report any unclear tools/context in your limitations."
                            if role == "journey"
                            else "Read repository guidance and the frozen task context. "
                            "Ask the human whether the fixture label should be Amber or Teal "
                            "through ask_question, recommend Teal, then end the turn. Do not edit "
                            "or submit a result before the answer. This is the required outcome."
                        ),
                        stages=[
                            Stage(id="clarify", title="Clarify", outcome="Obtain the label choice"),
                            *(
                                [
                                    Stage(
                                        id="implement",
                                        title="Implement",
                                        outcome="Write the chosen label",
                                    ),
                                    Stage(
                                        id="verify", title="Verify", outcome="Verify the label file"
                                    ),
                                ]
                                if role == "journey"
                                else []
                            ),
                        ],
                    ),
                )
                task = workspace.publish_task(
                    project.id,
                    task.id,
                    TaskPublish(
                        expected_revision=task.revision,
                        completion="code" if role == "journey" else "report",
                    ),
                )
                AgentSettings(workspace).edit(
                    project.id,
                    "worker",
                    AgentSettingsEdit(
                        expected_revision=1,
                        selection=CHOICE,
                    ),
                    task.id,
                )
                run = service.execution.claim(project.id, initial, {initial: set()})
                assert run is not None
                print(json.dumps({"started": role, "trial": str(trial)}), flush=True)
                job = asyncio.create_task(service._execute(run, repo))
                service.jobs[run.id] = job
                job.add_done_callback(lambda done: service._job_finished(run.id, done))
                await settle(job)
                outcome = service.execution.get(project.id, run.id)
                report.update(status=outcome.status)
                assert outcome.status == "waiting_for_input", outcome.problem
                assert outcome.applied_agent == CHOICE
                assert service.execution.local(run.id)["native_cleanup_confirmed"]
                assert any(item["tool"] == "ask_question" and not item["failed"] for item in calls)
                assert {item["tool"] for item in calls} <= {
                    "read_context",
                    "search_context",
                    "update_stages",
                    "ask_question",
                    "submit_result",
                }
                if role == "journey":
                    await worker_journey(
                        service,
                        project.id,
                        task.id,
                        outcome,
                        repo,
                        initial,
                        permissions,
                        observed,
                        report,
                    )
            assert all(v.cleanup_confirmed for v in observed)
            if role not in {"continuity", "journey"}:
                assert len(observed) == 1
            assert (repo / "CLAUDE.md").read_text() == guidance
            if role not in {"continuity", "journey"}:
                assert baseline(repo) == initial
                assert not git(repo, "status", "--porcelain").strip()
            elif role == "continuity":
                assert git(repo, "status", "--porcelain").strip() == b"?? shipping-label.txt"
            else:
                assert baseline(repo) != initial and not git(repo, "status", "--porcelain").strip()
            report["passed"] = True
    except Exception as error:
        # No raw native diagnostics/account state in the report.
        frame = traceback.extract_tb(error.__traceback__)[-1]
        report.update(
            failure=type(error).__name__,
            failureLocation={"file": Path(frame.filename).name, "line": frame.lineno},
        )
    finally:
        await service.close()
        report.update(
            scopedCalls=calls,
            nativePermissions=permissions,
            observedModel=next(
                (v.native_model for v in observed if hasattr(v, "native_model")), None
            ),
            cleanupConfirmed=bool(observed) and all(v.cleanup_confirmed for v in observed),
            personalServicesStarted=False,
        )
        if not report["cleanupConfirmed"]:
            report["passed"] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bridge", type=Path)
    parser.add_argument(
        "--bundle", action="store_true", help="Install and test the original runtime bundle"
    )
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--trial-root", type=Path, required=True)
    parser.add_argument(
        "--role", choices=["coordinator", "worker", "continuity", "journey"], required=True
    )
    parser.add_argument("--codex-bundle", type=Path)
    parser.add_argument("--codex-native", type=Path)
    parser.add_argument("--invoke-live", action="store_true", required=True)
    args = parser.parse_args()
    if args.role == "journey" and not args.bundle:
        parser.error("The complete worker journey requires the original installed bundle")
    if args.role == "continuity" and not (args.bundle and args.codex_bundle and args.codex_native):
        parser.error("Continuity requires installed Claude and explicit Codex bundle/native paths")
    root = args.trial_root.resolve(strict=True)
    source = Path(__file__).resolve().parents[1]
    if source.parent in (root, *root.parents) or any(
        path in (root, *root.parents)
        for path in (Path("/tmp").resolve(), Path("/var/folders").resolve())
    ):
        parser.error("Use a dedicated trial root outside source and shared temporary directories")
    trial = root / f"h1-role-{args.role}-{uuid4().hex}"
    trial.mkdir(mode=0o700)
    report = asyncio.run(
        proof(
            trial,
            args.bridge.resolve(strict=True),
            args.native.resolve(strict=True),
            args.role,
            bundle=args.bundle,
            codex_bundle=args.codex_bundle.resolve(strict=True) if args.codex_bundle else None,
            codex_native=args.codex_native.resolve(strict=True) if args.codex_native else None,
        )
    )
    (trial / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"trial": str(trial), **report}, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
