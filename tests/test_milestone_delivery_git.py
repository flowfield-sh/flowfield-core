"""Milestone-delivery adapter contract, before service-level branch orchestration.

Use real separate checkouts to prove reuse of the existing delivery primitives.
These tests do not establish milestone approval, scheduling or restart behavior.
"""

from pathlib import Path

import pytest

from flowfield.adapters import git_checkout
from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import baseline, git
from flowfield.errors import ApplicationError


def commit(repository: Path, filename: str, content: str) -> str:
    (repository / filename).write_text(content)
    git(repository, "add", filename)
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        filename,
    )
    return baseline(repository)


def checkout(repository: Path, path: Path, source: str, branch: str | None = None) -> Path:
    git(
        repository,
        "worktree",
        "add",
        *(["-b", branch] if branch else ["--detach"]),
        str(path),
        source,
    )
    return path


def test_two_contributions_assemble_away_from_human_checkout_then_promote(tmp_path):
    repository = tmp_path / "project"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    base = commit(repository, "README.md", "Project\n")
    assembled = checkout(repository, tmp_path / "milestone", base, "flowfield/m-1")
    binding = git_checkout.binding(assembled)
    main_binding = git_checkout.binding(repository)
    first = checkout(repository, tmp_path / "worker-1", base)
    first_commit = commit(first, "first.txt", "First contribution\n")
    # Human edits do not prevent assembling work in the owned milestone checkout.
    (repository / "README.md").write_text("Human draft\n")
    with gitops.lock(repository):
        candidate = gitops.candidate(repository, base, first_commit, "first")
        gitops.apply(assembled, "flowfield/m-1", base, candidate, binding)
    assert baseline(repository) == base
    assert (repository / "README.md").read_text() == "Human draft\n"
    second = checkout(repository, tmp_path / "worker-2", candidate)
    assert (second / "first.txt").read_text() == "First contribution\n"
    second_commit = commit(second, "second.txt", "Dependent contribution\n")
    with gitops.lock(assembled):
        combined = gitops.candidate(repository, candidate, second_commit, "second")
        gitops.apply(assembled, "flowfield/m-1", candidate, combined, binding)
    assert baseline(repository) == base
    assert baseline(assembled) == combined
    assert not (repository / "first.txt").exists()
    with pytest.raises(ApplicationError, match="local edits"):
        gitops.apply(repository, "main", base, combined, main_binding)
    assert baseline(repository) == base
    assert (repository / "README.md").read_text() == "Human draft\n"
    # Only the simulated human resolves their own known fixture edit.
    (repository / "README.md").write_text("Project\n")
    with gitops.lock(repository):
        gitops.apply(repository, "main", base, combined, main_binding)
    assert baseline(repository) == combined
    assert (repository / "second.txt").read_text() == "Dependent contribution\n"
    assert baseline(assembled) == combined
    assert git_checkout.binding(assembled) == binding


def test_diverged_destination_requires_verifying_the_combined_candidate(tmp_path):
    repository = tmp_path / "project"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    base = commit(repository, "README.md", "Project\n")
    assembled = checkout(repository, tmp_path / "milestone", base, "flowfield/m-1")
    source = commit(assembled, "feature.txt", "Milestone feature\n")
    before = commit(repository, "other.txt", "Unrelated delivered work\n")
    with gitops.lock(repository):
        combined = gitops.candidate(repository, before, source, "promotion")
    assert combined not in (source, before)
    inspection = checkout(repository, tmp_path / "inspection", combined)
    assert (inspection / "feature.txt").read_text() == "Milestone feature\n"
    assert (inspection / "other.txt").read_text() == "Unrelated delivered work\n"
    assert baseline(repository) == before
    assert baseline(assembled) == source
    binding = git_checkout.binding(repository)
    # A destination update invalidates the old candidate even when ff-only alone
    # could apply some newer combination. Existing human files are preserved.
    moved = commit(repository, "later.txt", "Destination moved during review\n")
    with pytest.raises(ApplicationError, match="destination moved"):
        gitops.apply(repository, "main", before, combined, binding)
    assert baseline(repository) == moved
    assert not (repository / "feature.txt").exists()
    assert baseline(inspection) == combined


def test_milestone_and_project_checkouts_share_one_repository_lock(tmp_path):
    repository = tmp_path / "project"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    base = commit(repository, "README.md", "Project\n")
    assembled = checkout(repository, tmp_path / "milestone", base, "flowfield/m-1")
    with gitops.lock(repository), pytest.raises(ApplicationError, match="owns this repository"):
        with gitops.lock(assembled):
            pytest.fail("Linked checkouts must not acquire independent delivery locks")
    with gitops.lock(assembled):
        pass
