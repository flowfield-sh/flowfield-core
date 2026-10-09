"""Real Git result/approval/delivery invariants; no model calls."""

import asyncio
import os
from pathlib import Path

import pytest
from project_fixtures import adopt, task_request

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskProgress, TaskPublish, Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, SettingsEdit, WorkerResult
from flowfield.integration_models import IntegrationApply, IntegrationConfig
from flowfield.result_models import ResultReview
from flowfield.supervisor import Supervisor


def fixture(
    tmp_path: Path,
    *,
    completion="code",
    changed=True,
    checks=None,
    outcome="complete",
    remaining_work="",
):
    workspace = Workspace(tmp_path / "state")
    repo = tmp_path / "harbor"
    adopt(workspace, ProjectSetup(path=str(repo)))
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("base\n")
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
    service = Supervisor(workspace)
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="integration",
            create_from="main",
            checks=checks or ["test -f result.txt"],
        ),
    )
    git(repo, "switch", "integration")
    task = workspace.create_task(
        "harbor", task_request(id="work", title="Work", body="Deliver the result", status="up_next")
    )
    workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            completion=completion,
            expected_revision=task.revision,
        ),
    )
    settings = service.execution.configure(
        "harbor",
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="fixture", effort="low")),
    )
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    assert run
    service.execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(workspace.directory, repo, run.id, base)
    if changed:
        (env.checkout / "result.txt").write_text("implemented\n")
    commit, _ = env.snapshot(base)
    run = service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(
            summary="Outcome",
            checks="Worker-reported checks",
            outcome=outcome,
            remaining_work=remaining_work,
        ),
        commit=commit,
    )
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    return service, repo, run


def current(service):
    return service.results.page("harbor", "work").items[0]


def test_task_history_pages_one_chronology_and_nests_service_evidence(tmp_path):
    from flowfield.activity import ActivityCreate
    from flowfield.execution_history import ExecutionHistory

    service, _, run = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    service.results.process("harbor")
    workspace = service.workspace
    note = workspace.add_activity("harbor", ActivityCreate(task_id="work", body="Keep this note"))
    decision = workspace.add_activity(
        "harbor",
        ActivityCreate(
            task_id="work",
            kind="handoff",
            expected_task_revision=workspace.task("harbor", "work").revision,
            supersedes=None,
            body="First handoff",
        ),
    )
    replacement = workspace.add_activity(
        "harbor",
        ActivityCreate(
            task_id="work",
            kind="handoff",
            expected_task_revision=workspace.task("harbor", "work").revision,
            body="Current handoff",
            supersedes=decision.id,
        ),
    )
    workspace.create_task("harbor", task_request(id="other", title="Unrelated"))
    foreign = workspace.add_activity("harbor", ActivityCreate(task_id="other", body="Other task"))
    history = ExecutionHistory(workspace)
    full = history.timeline("harbor", "work", limit=50).items
    assert full[0].id == replacement.id
    assert foreign.id not in {item.id for item in full}
    assert [item.kind for item in full if hasattr(item, "run_id")] == ["worker"]
    paged = []
    offset = 0
    while True:
        page = history.timeline("harbor", "work", limit=2, offset=offset)
        paged.extend(page.items)
        if page.next_offset is None:
            break
        offset = page.next_offset
    assert [item.id for item in paged] == [item.id for item in full]
    assert {item.id for item in history.timeline("harbor", "work", mode="notes").items} == {
        note.id,
        decision.id,
        replacement.id,
    }
    assert [item.id for item in history.timeline("harbor", "work", mode="attempts").items] == [
        f"worker:{run.id}"
    ]
    assert {item.kind for item in history.page("harbor", "work", run_id=run.id).items} == {
        "worker",
        "validation",
        "delivery",
    }
    assert history.page("harbor", "other", run_id=run.id).items == []


def approve(service, version):
    return service.results.review(
        "harbor",
        version.id,
        ResultReview(
            expected_revision=version.revision,
            candidate_commit=version.candidate_commit or version.source_commit,
            action="approve",
        ),
    )


def test_validate_approve_deliver_and_idempotent_receipt(tmp_path):
    service, repo, run = fixture(tmp_path)
    assert current(service).status == "preparing"
    with pytest.raises(ApplicationError, match="successful candidate validation"):
        approve(service, current(service))
    service.results.process("harbor")
    ready = current(service)
    assert ready.status == "ready", ready.problem
    assert service.integrations.head("harbor") == run.base_commit
    (repo / "README.md").write_text("human dirty work")
    delivery = approve(service, ready)
    assert delivery.status == "delivering"
    assert service.workspace.task("harbor", "work").status == "in_review"
    assert approve(service, ready).id == delivery.id
    service.results.process("harbor")
    blocked = current(service)
    assert blocked.problem_code == "checkout_dirty"
    assert blocked.approved_at == delivery.approved_at
    assert service.workspace.task("harbor", "work").status == "in_review"
    assert baseline(repo) == run.base_commit
    assert (repo / "README.md").read_text() == "human dirty work"
    (repo / "README.md").write_text("base\n")
    from flowfield.execution_models import RunAction

    service.results.retry_delivery(
        "harbor", blocked.id, RunAction(expected_revision=blocked.revision)
    )
    service.results.process("harbor")
    assert current(service).status == "delivered"
    assert service.workspace.task("harbor", "work").status == "done"
    assert service.integrations.head("harbor") == ready.candidate_commit
    assert baseline(repo) == ready.candidate_commit
    assert (repo / "result.txt").read_text() == "implemented\n"
    assert approve(service, ready).status == "delivered"
    assert len(service.integrations.page("harbor").items) == 1


def test_raw_apply_and_progress_cannot_bypass_approval(tmp_path):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    record = service.integrations.get("harbor", version.integration_id)
    with pytest.raises(ApplicationError, match="Approve this exact"):
        service.integrations.apply(
            "harbor",
            record.id,
            IntegrationApply(
                expected_revision=record.revision, candidate_commit=record.candidate_commit
            ),
        )
    task = service.workspace.task("harbor", "work")
    with pytest.raises(ApplicationError, match="manual progress"):
        service.workspace.record_progress(
            "harbor", task.id, TaskProgress(expected_revision=task.revision, status="done")
        )


def test_failed_validation_never_becomes_approvable(tmp_path):
    service, _, run = fixture(tmp_path, checks=["exit 7"])
    service.results.process("harbor")
    version = current(service)
    assert version.status == "blocked"
    with pytest.raises(ApplicationError, match="successful candidate validation"):
        approve(service, version)
    assert service.integrations.head("harbor") == run.base_commit
    assert service.workspace.task("harbor", "work").status == "in_review"


def test_target_moves_after_approval_leaves_unfinished_delivery(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    git(repo, "update-ref", "refs/heads/integration", run.result_commit, run.base_commit)
    service.results.process("harbor")
    assert current(service).status == "stale"
    assert service.workspace.task("harbor", "work").status == "in_review"


@pytest.mark.parametrize("enabled", [False, True])
def test_requested_changes_explain_observed_queue_state(tmp_path, enabled):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    settings = service.execution.settings("harbor")
    service.execution.queue(
        "harbor", QueueEdit(expected_revision=settings.revision, enabled=enabled)
    )
    service.results.review(
        "harbor",
        version.id,
        ResultReview(
            expected_revision=version.revision,
            candidate_commit=version.candidate_commit,
            action="request_changes",
            note="Refine",
        ),
    )
    action = current(service).next_action
    assert action.label == ("Waiting for capacity" if enabled else "Paused")
    assert ("queue is paused" in action.reason) is not enabled
    assert ("when capacity is available" in action.reason) is enabled


@pytest.mark.parametrize("restart", [True, False])
def test_restart_recovers_git_update_before_database_completion(tmp_path, monkeypatch, restart):
    service, repo, _ = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    actual = gitops.apply

    def crash(*args):
        actual(*args)
        raise RuntimeError("simulated process crash")

    monkeypatch.setattr(gitops, "apply", crash)
    with pytest.raises(RuntimeError, match="simulated"):
        service.results.process("harbor")
    assert service.workspace.task("harbor", "work").status == "in_review"
    fresh = Supervisor(Workspace(service.workspace.directory))
    if restart:
        fresh.integrations.restart()
    fresh.results.process("harbor")
    assert current(fresh).status == "delivered"
    assert fresh.workspace.task("harbor", "work").status == "done"
    assert len(git(repo, "reflog", "show", "integration").splitlines()) <= 2


def test_manual_completion_requires_explicit_report(tmp_path):
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "harbor")))
    with pytest.raises(ApplicationError, match="Create the task"):
        workspace.create_task("harbor", task_request(title="Bypass", status="done"))
    task = workspace.create_task("harbor", task_request(title="Report"))
    with pytest.raises(ApplicationError, match="explicitly accepted reports"):
        workspace.record_progress(
            "harbor", task.id, TaskProgress(expected_revision=1, status="done")
        )
    assert (
        workspace.record_progress(
            "harbor", task.id, TaskProgress(expected_revision=1, status="done", completion="report")
        ).status
        == "done"
    )


def test_service_finishes_approved_delivery_with_queue_paused(tmp_path):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))

    async def journey():
        fresh = Supervisor(Workspace(service.workspace.directory))
        await fresh.start()
        try:
            for _ in range(100):
                if current(fresh).status == "delivered":
                    break
                await asyncio.sleep(0.02)
            assert current(fresh).status == "delivered"
            assert not fresh.execution.settings("harbor").enabled
        finally:
            await fresh.close()

    asyncio.run(journey())


def test_report_completion_and_report_cannot_smuggle_code(tmp_path):
    service, _, _ = fixture(tmp_path / "report", completion="report", changed=False)
    service.results.process("harbor")
    assert current(service).status == "delivered"
    assert current(service).approved_at is None
    assert service.workspace.task("harbor", "work").status == "done"
    assert service.integrations.page("harbor").items == []
    changed, _, _ = fixture(tmp_path / "code", completion="report")
    changed.results.process("harbor")
    assert current(changed).status == "blocked"
    assert "includes code changes" in current(changed).problem


def test_approval_comment_is_bound_persisted_and_replayed_without_overwrite(tmp_path):
    from flowfield.thread_view import ThreadView

    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    request = ResultReview(
        expected_revision=version.revision,
        candidate_commit=version.candidate_commit,
        action="approve",
        note="Keyboard checked; narrow view not tested.",
    )
    with pytest.raises(ApplicationError):
        service.results.review(
            "harbor", version.id, request.model_copy(update={"candidate_commit": "0" * 40})
        )
    assert not current(service).approved_at
    approved = service.results.review("harbor", version.id, request)
    assert approved.approval_note == request.note
    replay = service.results.review(
        "harbor", version.id, request.model_copy(update={"note": "Different note"})
    )
    assert replay.approval_note == request.note
    restored = Supervisor(Workspace(service.workspace.directory))
    entry = ThreadView(restored.workspace).page_view("harbor", "work")
    assert next(item for item in entry.items if item.kind == "approval").body == request.note
