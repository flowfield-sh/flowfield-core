"""Checkout protection and interrupted delivery, using real disposable Git repositories."""

import pytest
from result_fixtures import approve, current, fixture

from flowfield.adapters import git_checkout
from flowfield.adapters.git_workspace import baseline, git
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.integration_models import IntegrationConfig
from flowfield.supervisor import Supervisor


def ready(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    return service, repo, run


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
    assert service.integrations.head("harbor") == run.base_commit
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
