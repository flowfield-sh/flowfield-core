"""Real local remotes prove automatic freshness without models or network access."""

from pathlib import Path

import pytest

from flowfield.adapters import git_integration, git_upstream
from flowfield.adapters.git_workspace import GitWorkspace, baseline, git
from flowfield.errors import ApplicationError


def commit(repository: Path, name: str, content: str) -> str:
    (repository / name).write_text(content)
    git(repository, "add", name)
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        name,
    )
    return baseline(repository)


@pytest.fixture
def repositories(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-b", "release")
    commit(remote, "base.txt", "base")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(remote), str(checkout))
    git(checkout, "branch", "-m", "main")
    return remote, checkout


def refresh(repository):
    with git_integration.lock(repository):
        return git_upstream.refresh(repository, "main")


def test_refresh_tracks_remote_branch_and_preserves_pinned_attempt(repositories, tmp_path):
    remote, checkout = repositories
    old = baseline(checkout)
    attempt = GitWorkspace.prepare(tmp_path / "attempt", checkout, old)
    newer = commit(remote, "new.txt", "new release")
    assert git(checkout, "rev-parse", "origin/release").decode().strip() == old
    assert refresh(checkout) == newer
    assert baseline(checkout) == newer
    assert git(checkout, "rev-parse", "origin/release").decode().strip() == newer
    assert (checkout / "new.txt").read_text() == "new release"
    assert baseline(attempt.checkout) == old
    assert not (attempt.checkout / "new.txt").exists()
    assert refresh(checkout) == newer


def test_ahead_local_work_is_preserved_and_divergence_is_explicit(repositories):
    remote, checkout = repositories
    local = commit(checkout, "local.txt", "delivered but not pushed")
    assert refresh(checkout) == local
    upstream = commit(remote, "remote.txt", "parallel release")
    with pytest.raises(ApplicationError) as failure:
        refresh(checkout)
    assert failure.value.code == "upstream_diverged"
    assert baseline(checkout) == local
    assert (checkout / "local.txt").read_text() == "delivered but not pushed"
    assert git(checkout, "cat-file", "-t", upstream).strip() == b"commit"


@pytest.mark.parametrize("ignored", [False, True])
def test_refresh_preserves_local_edits_and_ignored_collisions(repositories, ignored):
    remote, checkout = repositories
    old = baseline(checkout)
    name = "new.txt" if ignored else "base.txt"
    if ignored:
        (checkout / ".git/info/exclude").write_text("new.txt\n")
    (checkout / name).write_text("human work")
    commit(remote, name, "upstream work")
    with pytest.raises(ApplicationError):
        refresh(checkout)
    assert baseline(checkout) == old
    assert (checkout / name).read_text() == "human work"


def test_other_branch_is_not_switched_or_modified(repositories):
    remote, checkout = repositories
    old = baseline(checkout)
    git(checkout, "switch", "-c", "human-work")
    (checkout / "base.txt").write_text("dirty human work")
    commit(remote, "new.txt", "upstream")
    with pytest.raises(ApplicationError) as failure:
        refresh(checkout)
    assert failure.value.code == "checkout_branch_changed"
    assert git_integration.target(checkout, "main") == old
    assert baseline(checkout) == old
    assert (checkout / "base.txt").read_text() == "dirty human work"
    assert git(checkout, "branch", "--show-current").strip() == b"human-work"


def test_occupied_other_worktree_is_not_moved(repositories, tmp_path):
    remote, checkout = repositories
    old = baseline(checkout)
    git(checkout, "switch", "-c", "human-work")
    other = tmp_path / "other"
    git(checkout, "worktree", "add", str(other), "main")
    commit(remote, "new.txt", "upstream")
    with pytest.raises(ApplicationError) as failure:
        refresh(checkout)
    assert failure.value.code == "checkout_branch_changed"
    assert git_integration.target(checkout, "main") == old
    assert baseline(other) == old


def test_unavailable_upstream_never_silently_uses_cached_head(repositories):
    remote, checkout = repositories
    old = baseline(checkout)
    git(checkout, "remote", "set-url", "origin", str(remote / "missing"))
    with pytest.raises(ApplicationError) as failure:
        refresh(checkout)
    assert failure.value.code == "upstream_unavailable"
    assert baseline(checkout) == old


def test_local_only_branch_needs_no_remote(repositories):
    _, checkout = repositories
    git(checkout, "branch", "--unset-upstream", "main")
    git(checkout, "remote", "remove", "origin")
    assert refresh(checkout) == baseline(checkout)


def test_fetch_uses_native_url_configuration_but_never_repository_hooks(
    repositories, tmp_path, monkeypatch
):
    remote, checkout = repositories
    home = tmp_path / "home"
    home.mkdir()
    git(
        home,
        "config",
        "--file",
        str(home / ".gitconfig"),
        f"url.{remote}.insteadOf",
        "native-test:repository",
    )
    monkeypatch.setenv("HOME", str(home))
    git(checkout, "remote", "set-url", "origin", "native-test:repository")
    marker = tmp_path / "hook-ran"
    hook = checkout / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o700)
    newer = commit(remote, "new.txt", "upstream")
    assert refresh(checkout) == newer
    assert not marker.exists()


def test_result_validation_and_delivery_observe_new_upstream_without_reusing_approval(tmp_path):
    from result_fixtures import approve, current, fixture

    service, checkout, run = fixture(tmp_path)
    remote = tmp_path / "upstream"
    git(tmp_path, "clone", str(checkout), str(remote))
    git(checkout, "remote", "add", "origin", str(remote))
    git(checkout, "config", "branch.integration.remote", "origin")
    git(checkout, "config", "branch.integration.merge", "refs/heads/integration")
    newer = commit(remote, "first-release.txt", "first release")
    service.results.process("harbor")
    validated = current(service)
    assert validated.status == "ready" and validated.target_before == newer
    assert git(checkout, "show", validated.candidate_commit + ":result.txt") == b"implemented\n"
    assert service.execution.get("harbor", run.id).base_commit == run.base_commit
    approved = approve(service, validated)
    newest = commit(remote, "second-release.txt", "second release")
    service.results.process("harbor")
    stale = current(service)
    assert stale.status == "stale" and stale.problem_code == "target_changed"
    assert baseline(checkout) == newest
    assert stale.id == approved.id
    assert not (checkout / "result.txt").exists()


def test_fetch_preserves_configured_native_ssh_transport(repositories, tmp_path, monkeypatch):
    import shlex

    remote, checkout = repositories
    home = tmp_path / "home"
    home.mkdir()
    transport = tmp_path / "ssh-transport"
    transport.write_text(f"#!/bin/sh\nexec git-upload-pack {shlex.quote(str(remote))}\n")
    transport.chmod(0o700)
    git(home, "config", "--file", str(home / ".gitconfig"), "core.sshCommand", str(transport))
    git(home, "config", "--file", str(home / ".gitconfig"), "ssh.variant", "ssh")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    git(checkout, "remote", "set-url", "origin", "ssh://fixture.invalid/project")
    newer = commit(remote, "new.txt", "upstream")
    assert refresh(checkout) == newer


def test_scheduler_fetches_before_claim_and_bounds_retries(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from execution_fixtures import fixture

    from flowfield.integration_models import IntegrationConfig
    from flowfield.supervisor import Supervisor

    execution = fixture(tmp_path)
    checkout = tmp_path / "harbor"
    git(checkout, "init", "-b", "main")
    git(checkout, "add", ".")
    commit(checkout, "base.txt", "base")
    remote = tmp_path / "upstream"
    git(tmp_path, "clone", str(checkout), str(remote))
    git(checkout, "remote", "add", "origin", str(remote))
    git(checkout, "config", "branch.main.remote", "origin")
    git(checkout, "config", "branch.main.merge", "refs/heads/main")
    newer = commit(remote, "new.txt", "new release")
    service = Supervisor(execution.workspace)
    service.integrations.configure(
        "harbor", IntegrationConfig(expected_revision=1, target_branch="main")
    )
    monkeypatch.setattr(service, "_process_delivery", lambda _: None)
    monkeypatch.setattr(service.review_execution, "schedule", AsyncMock())
    monkeypatch.setattr(service, "validate_agent_choice", AsyncMock())
    monkeypatch.setattr(service, "_execute", AsyncMock())
    calls = []
    original = service.integrations.refresh_source

    def unavailable(*args):
        calls.append(args)
        raise ApplicationError("upstream_unavailable", "Unavailable")

    monkeypatch.setattr(service.integrations, "refresh_source", unavailable)

    async def schedule():
        await service._schedule_once()
        await asyncio.gather(*service.delivery_jobs.values(), *service.jobs.values())
        if service.cleanup_job:
            await service.cleanup_job

    asyncio.run(schedule())
    asyncio.run(schedule())
    assert len(calls) == 1 and execution.page("harbor").items == []
    monkeypatch.setattr(service.integrations, "refresh_source", original)
    key = ("harbor", "task-0")
    revision, _, error = service.source_checks[key]
    service.source_checks[key] = (revision, 0, error)  # Elapsed retry delay.
    asyncio.run(schedule())
    runs = execution.page("harbor").items
    assert len(runs) == 1 and runs[0].base_commit == newer
    assert baseline(checkout) == newer


def test_fetch_timeout_stops_owned_transport_children(repositories, tmp_path, monkeypatch):
    import os
    import shlex

    from flowfield.adapters import git_workspace

    _, checkout = repositories
    pid_file = tmp_path / "child.pid"
    transport = tmp_path / "slow-ssh"
    transport.write_text(
        '#!/bin/sh\ntrap \'kill "$child" 2>/dev/null; wait "$child"; exit 0\' TERM\n'
        f"sleep 60 &\nchild=$!\necho $child > {shlex.quote(str(pid_file))}\nwait $child\n"
    )
    transport.chmod(0o700)
    git(checkout, "config", "core.sshCommand", str(transport))
    git(checkout, "config", "ssh.variant", "ssh")
    git(checkout, "remote", "set-url", "origin", "ssh://fixture.invalid/project")
    monkeypatch.setattr(git_workspace, "FETCH_TIMEOUT_SECONDS", 0.5)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    with pytest.raises(ApplicationError) as failure:
        refresh(checkout)
    assert failure.value.code == "upstream_unavailable"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
