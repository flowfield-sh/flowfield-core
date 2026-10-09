"""Real Git journeys with deterministic local checks; no models or user repositories."""

import os
from pathlib import Path

import pytest
from project_fixtures import fixture_stage_change
from test_execution import fixture

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.application import TaskEdit, TaskPublish
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction, WorkerResult
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationApply, IntegrationConfig
from flowfield.result_models import ResultReview
from flowfield.results import Results

CHECK = "python -B -c 'from loader import load; assert load() == 42'"


def seed(tmp_path: Path, checks: list[str] | None = None):
    execution = fixture(tmp_path, count=2)
    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("Original project\n")
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
    integration.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="integration",
            create_from="main",
            checks=checks or [CHECK],
        ),
    )
    git(repo, "switch", "integration")
    for task in execution.workspace.tasks("harbor"):
        execution.workspace.publish_task(
            "harbor",
            task.id,
            TaskPublish(
                stages=fixture_stage_change(execution.workspace, "harbor", task.id),
                completion="code",
                expected_revision=task.revision,
            ),
        )
    base = baseline(repo)
    run = execution.claim("harbor", base, {base: set()})
    assert run
    execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(execution.workspace.directory, repo, run.id, base)
    (env.checkout / "loader.py").write_text("def load():\n    return 42\n")
    result, _ = env.snapshot(base)
    run = execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary="Loader", checks=CHECK),
        commit=result,
    )
    return integration, execution, repo, run


def prepare(service, run):
    results = Results(service.workspace)
    version = results.page("harbor", run.task_id).items[0]
    if version.status != "preparing":
        results.reprepare("harbor", version.id, RunAction(expected_revision=version.revision))
    results.process("harbor")
    version = results.page("harbor", run.task_id).items[0]
    return service.get("harbor", version.integration_id)


def authorize(service, candidate):
    results = Results(service.workspace)
    run = service.execution.get("harbor", candidate.run_id)
    version = results.page("harbor", run.task_id).items[0]
    if version.integration_id != candidate.id:
        raise ApplicationError("stale", "Candidate is not current", 409)
    if not version.approved_at:
        assert version.status == "ready", version.problem
        results.review(
            "harbor",
            version.id,
            ResultReview(
                expected_revision=version.revision,
                candidate_commit=candidate.candidate_commit,
                action="approve",
            ),
        )


def apply(service, candidate):
    authorize(service, candidate)
    return service.apply(
        "harbor",
        candidate.id,
        IntegrationApply(
            expected_revision=candidate.revision, candidate_commit=candidate.candidate_commit
        ),
    )


def advance(repo: Path, parent: str, content: dict[str, str], tmp_path: Path) -> str:
    work = tmp_path / "advanced"
    git(repo, "worktree", "add", "--detach", str(work), parent)
    for path, value in content.items():
        (work / path).write_text(value)
    git(work, "add", ".")
    git(
        work,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "target advanced",
    )
    commit = baseline(work)
    git(repo, "merge", "--ff-only", commit)
    return commit


def test_accept_validate_apply_releases_dependent_without_touching_human_files(tmp_path):
    service, execution, repo, run = seed(tmp_path)
    task = execution.workspace.task("harbor", "task-1")
    task = execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, dependencies=[run.task_id])
    )
    execution.workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            stages=fixture_stage_change(execution.workspace, "harbor", task.id),
            completion="code",
            expected_revision=task.revision,
        ),
    )
    original = baseline(repo)
    board = BrowserReads(execution.workspace).board("harbor")
    assert not board.pending_code[task.id]  # The prerequisite is unfinished, not Done without code.
    (repo / "README.md").write_text("Human dirty work\n")
    (repo / "notes.txt").write_text("Do not touch")
    assert execution.claim("harbor", service.head("harbor"), {original: set()}) is None
    candidate = prepare(service, run)
    assert candidate.status == "ready" and candidate.checks[0].exit_code == 0
    assert candidate.candidate_commit == run.result_commit
    assert service.head("harbor") == original and baseline(repo) == original
    with pytest.raises(ApplicationError, match="local edits"):
        apply(service, candidate)
    assert (repo / "README.md").read_text() == "Human dirty work\n"
    assert (repo / "notes.txt").read_text() == "Do not touch"
    (repo / "README.md").write_text("Original project\n")
    (repo / "notes.txt").unlink()
    integrated = apply(service, candidate)
    assert integrated.status == "integrated" and integrated.target_after == run.result_commit
    assert (repo / "loader.py").read_text() == "def load():\n    return 42\n"
    assert baseline(repo) == run.result_commit
    assert not BrowserReads(execution.workspace).board("harbor").pending_code[task.id]
    head = service.head("harbor")
    dependent = execution.claim("harbor", head, {head: service.available("harbor", head)})
    assert (
        dependent and dependent.task_id == "task-1" and dependent.base_commit == run.result_commit
    )
    assert execution.get("harbor", run.id).code_available
    with pytest.raises(ApplicationError):
        apply(service, candidate)


def test_advanced_target_combination_is_checked_and_stale_candidate_cannot_apply(tmp_path):
    service, execution, repo, run = seed(tmp_path)
    before = advance(repo, run.base_commit, {"other.txt": "unrelated"}, tmp_path)
    candidate = prepare(service, run)
    assert candidate.status == "ready", candidate.problem
    assert candidate.candidate_commit != run.result_commit
    assert gitops.contains(repo, before, candidate.candidate_commit)
    assert gitops.contains(repo, run.result_commit, candidate.candidate_commit)
    assert (Path(candidate.workspace) / "other.txt").read_text() == "unrelated"
    git(repo, "update-ref", "refs/heads/integration", run.base_commit, before)
    stale = apply(service, candidate)
    assert stale.status == "stale" and service.head("harbor") == run.base_commit
    candidate = prepare(service, run)
    git(repo, "update-ref", "refs/heads/integration", before, run.base_commit)
    service.refresh_availability("harbor")
    assert service.get("harbor", candidate.id).status == "stale"


def test_conflicts_and_failed_or_mutating_checks_preserve_target(tmp_path):
    service, execution, repo, run = seed(tmp_path)
    target = advance(repo, run.base_commit, {"loader.py": "def load():\n    return 99\n"}, tmp_path)
    conflict = prepare(service, run)
    assert conflict.status == "failed" and "combine" in conflict.problem
    assert service.head("harbor") == target
    git(repo, "update-ref", "refs/heads/integration", run.base_commit, target)
    settings = service.settings("harbor")
    service.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch="integration",
            checks=["exit 7"],
        ),
    )
    failed = prepare(service, run)
    assert failed.status == "failed" and failed.checks[0].exit_code == 7
    settings = service.settings("harbor")
    service.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch="integration",
            checks=["printf 'changed' > loader.py"],
        ),
    )
    changed = prepare(service, run)
    assert changed.status == "failed" and "new reviewed" in changed.problem
    assert Path(changed.workspace, "loader.py").read_text() == "changed"
    assert service.head("harbor") == run.base_commit


def test_checked_out_target_is_refused_and_unobserved_revert_invalidates_availability(tmp_path):
    service, execution, repo, run = seed(tmp_path)
    occupied = tmp_path / "occupied"
    git(repo, "worktree", "add", "--force", str(occupied), "integration")
    candidate = prepare(service, run)
    with pytest.raises(ApplicationError, match="another worktree"):
        apply(service, candidate)
    assert baseline(repo) == run.base_commit
    Results(execution.workspace).process("harbor")
    git(repo, "worktree", "remove", str(occupied))
    candidate = prepare(service, run)
    apply(service, candidate)
    # Revert the code while preserving result ancestry: ancestry alone must not release work.
    tree = git(repo, "rev-parse", run.base_commit + "^{tree}").decode().strip()
    reverted = (
        git(
            repo,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit-tree",
            tree,
            "-p",
            run.result_commit,
            "-m",
            "Revert loader",
        )
        .decode()
        .strip()
    )
    git(repo, "update-ref", "refs/heads/integration", reverted, run.result_commit)
    assert gitops.contains(repo, run.result_commit, reverted)
    assert service.refresh_availability("harbor") == set()
    assert not execution.get("harbor", run.id).code_available
    assert execution.workspace.task("harbor", run.task_id).status == "done"
    # Historical delivery is retained; current availability cannot rely on ancestry alone.


def test_restart_reconciles_applied_ref_but_never_applies_ready_work(tmp_path):
    service, execution, repo, run = seed(tmp_path)
    candidate = prepare(service, run)
    authorize(service, candidate)
    candidate.status = "applying"
    candidate.apply_started_at = "2026-09-30T12:00:00Z"
    service._save(candidate)
    gitops.apply(
        repo, "integration", candidate.target_before, candidate.candidate_commit, candidate.checkout
    )
    service.restart()
    assert service.get("harbor", candidate.id).status == "integrated"
    service.refresh_availability("harbor")
    assert execution.get("harbor", run.id).code_available
    other, _, _, pending_run = seed(tmp_path / "other")
    ready = prepare(other, pending_run)
    other.restart()
    assert other.get("harbor", ready.id).status == "ready"
    assert other.head("harbor") == pending_run.base_commit
    ready.status = "preparing"
    other._save(ready)
    other.restart()
    assert other.get("harbor", ready.id).status == "stale"


def test_browser_api_exposes_bounded_evidence_and_attention(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from flowfield.api import create_app
    from flowfield.supervisor import Supervisor

    async def idle(self):
        return None

    monkeypatch.setattr(Supervisor, "_schedule", idle)
    service, execution, repo, run = seed(tmp_path)
    with TestClient(
        create_app(data_dir=execution.workspace.directory), base_url="http://127.0.0.1"
    ) as client:
        Results(execution.workspace).process("harbor")
        version = Results(execution.workspace).page("harbor", run.task_id).items[0]
        prepared = client.get(f"/api/projects/harbor/integrations/{version.integration_id}")
        removed = client.post(
            f"/api/projects/harbor/runs/{run.id}/integrations",
            json={"expected_revision": run.revision},
        )
        assert removed.status_code == 404
        assert prepared.status_code == 200, prepared.text
        record = prepared.json()
        assert record["status"] == "ready"
        page = client.get("/api/projects/harbor/integrations").json()
        assert "checks" not in page["items"][0]
        waiting = client.get("/api/projects/harbor/view/attention?column=action").json()
        assert any(
            item["kind"] == "result" and item["run_id"] == run.id for item in waiting["items"]
        )
        files = client.get(f"/api/projects/harbor/integrations/{record['id']}/diff").json()
        assert any(f["new_path"] == "loader.py" for f in files["files"])
        version = client.get(f"/api/projects/harbor/tasks/{run.task_id}/results").json()["items"][0]
        approved = client.post(
            f"/api/projects/harbor/results/{version['id']}/review",
            json={
                "expected_revision": version["revision"],
                "candidate_commit": version["candidate_commit"],
                "action": "approve",
            },
        )
        assert approved.json()["status"] == "delivering"
        done = client.post(
            f"/api/projects/harbor/integrations/{record['id']}/apply",
            json={
                "expected_revision": record["revision"],
                "candidate_commit": record["candidate_commit"],
            },
        )
        assert done.status_code == 404
        Results(execution.workspace).process("harbor")
        assert (
            client.get(f"/api/projects/harbor/integrations/{record['id']}").json()["status"]
            == "integrated"
        )
        assert client.get("/api/projects/other/integrations/" + record["id"]).status_code == 404
        assert client.get("/api/projects/harbor/integrations?limit=51").status_code == 422


def test_target_compare_and_swap_and_changed_intent_cannot_be_bypassed(tmp_path, monkeypatch):
    service, execution, repo, run = seed(tmp_path)
    candidate = prepare(service, run)
    real_apply = gitops.apply
    moved = advance(repo, run.base_commit, {"other.txt": "new target"}, tmp_path)
    git(repo, "update-ref", "refs/heads/integration", run.base_commit, moved)
    git(repo, "restore", "--source", run.base_commit, "--staged", "--worktree", ".")

    def race(repository, branch, before, after, checkout):
        git(repository, "update-ref", "refs/heads/integration", moved, before)
        return real_apply(repository, branch, before, after, checkout)

    monkeypatch.setattr(gitops, "apply", race)
    result = apply(service, candidate)
    assert result.status == "stale" and service.head("harbor") == moved
    monkeypatch.setattr(gitops, "apply", real_apply)
    next_candidate = prepare(service, run)
    task = execution.workspace.task("harbor", run.task_id)
    execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, body="Changed agreement")
    )
    with pytest.raises(ApplicationError, match="older or blocked assignment"):
        apply(service, next_candidate)
    assert service.head("harbor") == moved


def test_enabled_scheduler_picks_up_dependent_after_explicit_integration(tmp_path, monkeypatch):
    import asyncio

    from test_supervisor import FakeWorker

    from flowfield.execution_models import QueueEdit
    from flowfield.supervisor import Supervisor

    service, execution, repo, run = seed(tmp_path)
    task = execution.workspace.task("harbor", "task-1")
    task = execution.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, dependencies=[run.task_id])
    )
    execution.workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            stages=fixture_stage_change(execution.workspace, "harbor", task.id),
            completion="code",
            expected_revision=task.revision,
        ),
    )

    class DependentWorker(FakeWorker):
        async def run(self, *args):
            assert (self.cwd / "loader.py").read_text().endswith("return 42\n")
            return await super().run(*args)

    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", DependentWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture-process")

    async def journey():
        supervisor = Supervisor(execution.workspace)
        await supervisor.start()
        settings = execution.settings("harbor")
        execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
        await asyncio.sleep(0.15)
        assert not execution.page("harbor", task_id=task.id).items
        for _ in range(100):
            version = supervisor.results.page("harbor", run.task_id).items[0]
            if version.status == "ready":
                break
            await asyncio.sleep(0.05)
        assert version.status == "ready", version.problem
        candidate = service.get("harbor", version.integration_id)
        authorize(service, candidate)
        for _ in range(100):
            runs = execution.page("harbor", task_id=task.id).items
            if runs and runs[0].status == "in_review":
                break
            await asyncio.sleep(0.05)
        assert runs and runs[0].status == "in_review"
        assert runs[0].base_commit == candidate.candidate_commit
        await supervisor.close()

    asyncio.run(journey())


def test_destination_save_creates_missing_branch_and_preserves_existing_branch(tmp_path):
    integration, _, repo, _ = seed(tmp_path)
    original = integration.settings("harbor")
    head = baseline(repo)
    request = IntegrationConfig(
        runtime="local",
        expected_revision=original.revision,
        target_branch="new-delivery",
        checks=[CHECK],
    )
    created = integration.configure("harbor", request)
    assert created.target_branch == "new-delivery"
    assert gitops.target(repo, "new-delivery") == head == baseline(repo)
    with pytest.raises(ApplicationError):
        integration.configure(
            "harbor",
            request.model_copy(
                update={"expected_revision": created.revision, "create_from": "main"}
            ),
        )
    assert integration.settings("harbor") == created
    # Reusing a destination never resets it to the source checkout's newer HEAD.
    (repo / "new-file.txt").write_text("human change\n")
    git(repo, "add", "new-file.txt")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Advance source checkout",
    )
    saved = integration.configure(
        "harbor", request.model_copy(update={"expected_revision": created.revision})
    )
    assert gitops.target(repo, "new-delivery") == head != baseline(repo)
    with pytest.raises(ApplicationError):
        integration.configure(
            "harbor", request.model_copy(update={"target_branch": "stale-destination"})
        )
    assert integration.settings("harbor") == saved
    with pytest.raises(ApplicationError) as error:
        gitops.target(repo, "stale-destination")
    assert error.value.code == "missing_destination"
