"""Checkout protection and interrupted delivery, using real disposable Git repositories."""

import pytest
from result_fixtures import current, fixture, ready

from flowfield.adapters import git_checkout
from flowfield.adapters.git_workspace import baseline, git
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.integration_models import IntegrationConfig
from flowfield.supervisor import Supervisor


@pytest.mark.parametrize("kind", ["staged", "untracked", "ignored", "operation", "branch"])
def test_checkout_blockers_preserve_files_approval_and_retry_same_result(tmp_path, kind):
    service, repo, run = ready(tmp_path)
    approved = current(service)
    if kind == "staged":
        (repo / "README.md").write_text("human edit\n")
        git(repo, "add", "README.md")
    elif kind in ("untracked", "ignored"):
        if kind == "ignored":
            (repo / ".git/info/exclude").write_text("result.txt\n")
        (repo / "result.txt").write_text("human file\n")
    elif kind == "operation":
        (repo / ".git/MERGE_HEAD").write_text(run.base_commit + "\n")
    else:
        git(repo, "switch", "main")
    service.results.process("harbor")
    blocked = current(service)
    assert blocked.status == "stale"
    assert blocked.approved_at == approved.approved_at
    assert blocked.next_action.action == "retry-delivery"
    assert service.workspace.task("harbor", "work").status == "in_review"
    assert service.integrations.head("harbor", "work") == run.base_commit
    if kind == "staged":
        assert (repo / "README.md").read_text() == "human edit\n"
        assert b"human edit" in git(repo, "diff", "--cached")
        # The simulated human resolves only these known fixture edits.
        git(repo, "restore", "--staged", "--worktree", "README.md")
    elif kind in ("untracked", "ignored"):
        assert (repo / "result.txt").read_text() == "human file\n"
        (repo / "result.txt").unlink()
    elif kind == "operation":
        (repo / ".git/MERGE_HEAD").unlink()
    else:
        git(repo, "switch", "integration")
    service.results.retry_delivery(
        "harbor", blocked.id, RunAction(expected_revision=blocked.revision)
    )
    service.results.process("harbor")
    delivered = current(service)
    assert delivered.status == "delivered", delivered.problem
    assert delivered.id == approved.id and delivered.approved_at == approved.approved_at
    assert baseline(repo) == delivered.candidate_commit
    assert (repo / "result.txt").read_text() == "implemented\n"
    assert len(service.integrations.page("harbor").items) == 1


def test_reference_only_crash_cannot_masquerade_as_checkout_delivery(tmp_path):
    service, repo, run = ready(tmp_path)
    record = service.integrations.get("harbor", current(service).integration_id)
    record.status, record.apply_started_at = "applying", "2026-09-30T12:00:00Z"
    service.integrations._save(record)
    git(repo, "update-ref", "refs/heads/integration", record.candidate_commit, run.base_commit)
    fresh = Supervisor(Workspace(service.workspace.directory))
    fresh.integrations.restart()
    fresh.results.process("harbor")
    assert current(fresh).status == "stale"
    assert current(fresh).problem_code == "delivery_uncertain"
    assert fresh.workspace.task("harbor", "work").status == "in_review"
    assert not (repo / "result.txt").exists()


def test_git_transaction_rejects_target_moved_after_preflight(tmp_path, monkeypatch):
    _, repo, run = fixture(tmp_path)
    tree = git(repo, "rev-parse", run.result_commit + "^{tree}").decode().strip()
    after = (
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
            "Candidate",
        )
        .decode()
        .strip()
    )
    expected = git_checkout.binding(repo)
    real = git_checkout.git

    def race(repository, *args, **kwargs):
        if "merge" in args:
            # Move to another ancestor of the candidate: ff-only alone would accept it.
            git(repository, "merge", "--ff-only", run.result_commit)
        return real(repository, *args, **kwargs)

    monkeypatch.setattr(git_checkout, "git", race)
    with pytest.raises(ApplicationError):
        git_checkout.deliver(repo, "integration", run.base_commit, after, expected)
    assert baseline(repo) == run.result_commit != after


def test_restart_before_git_retries_durable_approved_intent(tmp_path):
    service, repo, _ = ready(tmp_path)
    record = service.integrations.get("harbor", current(service).integration_id)
    record.status, record.apply_started_at = "applying", "2026-09-30T12:00:00Z"
    service.integrations._save(record)
    fresh = Supervisor(Workspace(service.workspace.directory))
    fresh.integrations.restart()
    fresh.results.process("harbor")
    assert current(fresh).status == "delivered", current(fresh).problem
    assert baseline(repo) == record.candidate_commit


@pytest.mark.parametrize("change", ["settings", "target"])
def test_checkout_retry_cannot_transfer_approval_to_changed_destination(tmp_path, change):
    service, repo, run = ready(tmp_path)
    (repo / "local.txt").write_text("human file")
    service.results.process("harbor")
    blocked = current(service)
    assert blocked.next_action.action == "retry-delivery"
    if change == "settings":
        settings = service.integrations.settings("harbor")
        service.integrations.configure(
            "harbor",
            IntegrationConfig(
                runtime="local",
                expected_revision=settings.revision,
                target_branch="integration",
                checks=["true"],
            ),
        )
    else:
        from result_recovery_fixtures import target_change

        target_change(service, repo, run.base_commit)
    before = baseline(repo)
    updated = service.results.retry_delivery(
        "harbor", blocked.id, RunAction(expected_revision=blocked.revision)
    )
    assert updated.problem_code == f"{change}_changed"
    assert updated.next_action.action != "retry-delivery"
    service.results.process("harbor")
    assert current(service).status == "stale"
    assert baseline(repo) == before
    assert (repo / "local.txt").read_text() == "human file"
    assert not (repo / "result.txt").exists()


def test_missing_transaction_guard_refuses_before_changing_files(tmp_path, monkeypatch):
    _, repo, run = fixture(tmp_path)
    real = git_checkout.git

    def unsupported(repository, *args, **kwargs):
        if "update-ref" in args and "--stdin" in args:
            return b""  # An older Git silently omits the reference hook.
        return real(repository, *args, **kwargs)

    monkeypatch.setattr(git_checkout, "git", unsupported)
    with pytest.raises(ApplicationError, match="transaction guard"):
        git_checkout.deliver(
            repo, "integration", run.base_commit, run.result_commit, git_checkout.binding(repo)
        )
    assert baseline(repo) == run.base_commit
    assert not (repo / "result.txt").exists()


@pytest.mark.parametrize("unavailable", ["missing", "locked"])
@pytest.mark.parametrize("git_applied", [False, True])
def test_unavailable_recovery_starts_service_and_preserves_exact_retry(
    tmp_path, unavailable, git_applied
):
    import asyncio
    from contextlib import nullcontext

    from workspace_fixtures import TEST_AGENTS

    from flowfield.adapters import git_integration
    from flowfield.application import TaskCreate
    from flowfield.workspaces import WorkspaceCreate, Workspaces

    service, repo, run = ready(tmp_path)
    approved = current(service)
    record = service.integrations.get("harbor", approved.integration_id)
    record.status, record.apply_started_at = "applying", "2026-11-01T12:00:00Z"
    service.integrations._save(record)
    if git_applied:
        git_checkout.deliver(
            repo,
            record.target_branch,
            record.target_before,
            record.candidate_commit,
            record.checkout,
        )
    other = Workspaces(service.workspace).create(WorkspaceCreate(**TEST_AGENTS, name="Unaffected"))
    task = service.workspace.create_task(other.id, TaskCreate(title="Unrelated work"))
    moved = repo.with_name("unavailable")
    if unavailable == "missing":
        repo.rename(moved)
    fresh = Supervisor(Workspace(service.workspace.directory))

    async def restart():
        await fresh.start()
        try:
            assert fresh.loop_task is not None and not fresh.loop_task.done()
            assert fresh.workspace.task(other.id, task.id).title == "Unrelated work"
            blocked = current(fresh)
            assert blocked.approved_at == approved.approved_at
            assert blocked.candidate_commit == approved.candidate_commit
            if unavailable == "missing":
                assert blocked.status == "stale"
                assert blocked.problem_code == "delivery_uncertain"
                assert blocked.next_action.action == "retry-delivery"
            else:
                assert blocked.status == "delivering"
                assert fresh.integrations.get("harbor", record.id).revision == record.revision
        finally:
            await fresh.close()

    try:
        with git_integration.lock(repo) if unavailable == "locked" else nullcontext():
            asyncio.run(restart())
    finally:
        if unavailable == "missing":
            moved.rename(repo)
    blocked = current(fresh)
    if unavailable == "missing":
        fresh.results.retry_delivery(
            "harbor", blocked.id, RunAction(expected_revision=blocked.revision)
        )
    fresh.results.process("harbor")
    fresh.results.process("harbor")
    delivered = current(fresh)
    assert delivered.status == "delivered", delivered.problem
    assert delivered.id == approved.id and delivered.approved_at == approved.approved_at
    assert baseline(repo) == approved.candidate_commit
    assert len(fresh.integrations.page("harbor").items) == 1


def test_recovery_does_not_hide_database_failures(tmp_path, monkeypatch):
    import sqlite3

    service, _, _ = ready(tmp_path)
    record = service.integrations.get("harbor", current(service).integration_id)

    def fail(_):
        raise sqlite3.DatabaseError("database damaged")

    monkeypatch.setattr(service.integrations, "_recover_locked", fail)
    with pytest.raises(sqlite3.DatabaseError, match="damaged"):
        service.integrations.recover(record)


def test_recovery_does_not_overwrite_live_preparation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from flowfield.adapters import git_integration

    service, repo, run = fixture(tmp_path)
    entered, release = Event(), Event()
    original = git_integration.run_checks

    def checks(environment, commands, timeout=60, **kwargs):
        if commands:
            entered.set()
            assert release.wait(10)
        return original(environment, commands, timeout, **kwargs)

    monkeypatch.setattr(git_integration, "run_checks", checks)
    with ThreadPoolExecutor() as pool:
        work = pool.submit(service.results.process, "harbor")
        try:
            assert entered.wait(10)
            pending = current(service)
            integration = service.integrations.get("harbor", pending.integration_id)
            assert integration.status == "preparing"
            service.integrations.restart()
            service.results.process("harbor")
            assert service.integrations.get("harbor", integration.id) == integration
            assert current(service).revision == pending.revision
            assert current(service).status == "preparing"
        finally:
            release.set()
        work.result(10)
    assert current(service).status == "ready"
    assert baseline(repo) == run.base_commit
