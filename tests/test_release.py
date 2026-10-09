"""Release controls exercised with disposable Git repositories and no external publication."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


def load(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release():
    return load("release")


@pytest.fixture
def repository(tmp_path, monkeypatch, release):
    root, remote = tmp_path / "checkout", tmp_path / "remote.git"
    root.mkdir()

    def git(*args, cwd=root):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Release verification")
    git("config", "user.email", "release@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "tag.gpgsign", "false")
    git("init", "--bare", str(remote))
    git("remote", "add", "origin", str(remote))
    (root / "pyproject.toml").write_text('[project]\nname="flowfield-core"\nversion="0.1.0"\n')
    (root / "uv.lock").write_text('[[package]]\nname="flowfield-core"\nversion="0.1.0"\n')
    (root / "CHANGELOG.md").write_text("## 0.1.0\n\nReviewed first release.\n")
    (root / ".gitignore").write_text("release-notes.md\n")
    git("add", ".")
    git("commit", "-m", "Reviewed source")
    git("push", "origin", "main")
    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    return root, remote, git


@pytest.mark.parametrize("value", ["v0.1.0", "0.1", "01.1.0", "0.1.0rc1", "1.2.3\n", "a;exit"])
def test_final_versions_only(release, value):
    with pytest.raises(ValueError):
        release.version_tuple(value)


def test_first_release_forward_versions_and_unique_reviewed_notes(release):
    release.check_version("0.1.0", "0.1.0", [])
    release.check_version("0.1.1", "0.1.0", ["v0.1.0"])
    release.check_version("0.2.0", "0.1.0", ["v0.1.0"])
    release.check_version("1.0.0", "0.9.0", ["v0.9.0"])
    for requested, current, tags in [
        ("0.2.0", "0.1.0", []),
        ("0.1.0", "0.1.0", ["v0.1.0"]),
        ("0.1.1", "0.2.0", ["v0.1.0"]),
        ("0.2.0", "0.1.0", ["v0.3.0"]),
    ]:
        with pytest.raises(ValueError):
            release.check_version(requested, current, tags)
    notes = "## 0.2.0\n\nNew.\n### Upgrade\nMigrate.\n\n## 0.1.0\nOld.\n"
    assert release.release_notes(notes, "0.2.0") == "New.\n### Upgrade\nMigrate.\n"
    for invalid in ["## 0.2.0\n\n", notes + "## 0.2.0\nDuplicate.", "## 0.1.0\nOld."]:
        with pytest.raises(ValueError):
            release.release_notes(invalid, "0.2.0")


def test_ci_checks_exact_tag_main_ancestry_notes_and_lock(repository, release):
    root, _, git = repository
    git("tag", "v0.1.0")
    assert release.check_ci("push", "refs/tags/v0.1.0", release.REPOSITORY) == "0.1.0"
    assert (root / "release-notes.md").read_text() == "Reviewed first release.\n"
    assert release.check_ci("workflow_dispatch", "refs/heads/main", release.REPOSITORY) == "0.1.0"
    for event, ref, repo in [
        ("push", "refs/tags/v0.2.0", release.REPOSITORY),
        ("workflow_dispatch", "refs/tags/v0.1.0", release.REPOSITORY),
        ("push", "refs/tags/v0.1.0", "other/fork"),
    ]:
        with pytest.raises(ValueError):
            release.check_ci(event, ref, repo)
    (root / "uv.lock").write_text('[[package]]\nname="flowfield-core"\nversion="0.2.0"\n')
    with pytest.raises(ValueError, match="same Flowfield version"):
        release.check_ci("push", "refs/tags/v0.1.0", release.REPOSITORY)
    git("restore", "uv.lock")
    git("switch", "-c", "unmerged")
    (root / "feature.txt").write_text("Unmerged source")
    git("add", "feature.txt")
    git("commit", "-m", "Unmerged")
    git("tag", "-f", "v0.1.0")
    with pytest.raises(subprocess.CalledProcessError):
        release.check_ci("push", "refs/tags/v0.1.0", release.REPOSITORY)


@pytest.mark.parametrize(
    "mode", ["preview", "publish", "next", "failed_checks", "failed_push", "moved_head"]
)
def test_local_release_publication_and_failures(repository, release, monkeypatch, mode):
    root, remote, git = repository
    version = "0.1.0"
    if mode == "next":
        git("tag", "v0.1.0")
        git("push", "origin", "v0.1.0")
        version = "0.2.0"
        (root / "CHANGELOG.md").write_text("## 0.2.0\n\nReviewed feature.\n")
        git("add", "CHANGELOG.md")
        git("commit", "-m", "Review feature notes")
    initial = git("rev-parse", "HEAD")
    original = release.run
    calls = []

    def run(*args, capture=False, timeout=120):
        calls.append(args)
        if args[:3] == ("git", "remote", "get-url"):
            return f"git@github.com:{release.REPOSITORY}.git"
        if args[:2] == ("uv", "version"):
            for file in ["pyproject.toml", "uv.lock"]:
                (root / file).write_text((root / file).read_text().replace('"0.1.0"', '"0.2.0"'))
            return ""
        if args[0] == "make":
            if mode == "failed_checks":
                raise subprocess.CalledProcessError(1, args)
            if mode == "moved_head":
                git("commit", "--allow-empty", "-m", "Concurrent commit")
            return ""
        if args[:2] == ("git", "push") and mode == "failed_push":
            raise subprocess.CalledProcessError(1, args)
        if args[:3] == ("gh", "run", "list"):
            return json.dumps([{"databaseId": 1, "headBranch": f"v{version}", "url": "fixture"}])
        if args[0] == "gh":
            return ""
        return original(*args, capture=capture, timeout=timeout)

    monkeypatch.setattr(release, "run", run)
    if mode in {"failed_checks", "failed_push", "moved_head"}:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            release.publish(version, False)
    else:
        release.publish(version, mode == "preview")
    remote_tags = git("tag", "--list", cwd=remote).splitlines()
    if mode in {"publish", "next"}:
        assert f"v{version}" in remote_tags
        assert git("rev-parse", "refs/heads/main", cwd=remote) == git(
            "rev-parse", f"v{version}^{{}}"
        )
        push = next(call for call in calls if call[:2] == ("git", "push"))
        assert "--atomic" in push
        assert git("status", "--porcelain") == ""
        if mode == "next":
            assert set(git("diff", "--name-only", initial, "HEAD").splitlines()) == {
                "pyproject.toml",
                "uv.lock",
            }
    else:
        assert remote_tags == []
        assert git("tag", "--list").splitlines() == (["v0.1.0"] if mode == "failed_push" else [])
    if mode == "preview":
        assert git("rev-parse", "HEAD") == initial
        assert not any(call[0] in {"make", "uv", "gh"} for call in calls)


def test_preflight_refuses_dirty_wrong_remote_and_remote_only_tag(repository, release, monkeypatch):
    root, remote, git = repository
    with pytest.raises(ValueError, match="origin must"):
        release.preflight("0.1.0")
    original = release.run

    def run(*args, **kwargs):
        if args[:3] == ("git", "remote", "get-url"):
            return f"https://github.com/{release.REPOSITORY}.git"
        return original(*args, **kwargs)

    monkeypatch.setattr(release, "run", run)
    (root / "dirty.txt").write_text("Keep me")
    with pytest.raises(ValueError, match="Commit reviewed"):
        release.preflight("0.1.0")
    (root / "dirty.txt").unlink()
    git("tag", "v0.1.0", "refs/heads/main", cwd=remote)
    with pytest.raises(ValueError, match="exceed every"):
        release.preflight("0.1.0")
    assert not git("tag", "--list")


def test_artifact_selection_rejects_stale_or_extra_distributions(tmp_path, monkeypatch):
    checker = load("check_dist")
    names = ["flowfield_core-0.1.0-py3-none-any.whl", "flowfield_core-0.1.0.tar.gz"]
    for name in names:
        (tmp_path / name).touch()
    assert [file.name for file in checker.artifacts(tmp_path, "0.1.0")] == names
    (tmp_path / "flowfield_core-0.0.1.tar.gz").touch()
    with pytest.raises(ValueError, match="Unexpected distribution artifacts"):
        checker.artifacts(tmp_path, "0.1.0")
    monkeypatch.setenv("FLOWFIELD_DATA_DIR", "/valuable-state")
    monkeypatch.setenv("PYTHONPATH", "/checkout")
    monkeypatch.setenv("UV_TOOL_DIR", "/existing-tools")
    env = checker.environment()
    assert env["FLOWFIELD_UPDATE_CHECKS"] == "0"
    assert not {"FLOWFIELD_DATA_DIR", "PYTHONPATH", "UV_TOOL_DIR"} & env.keys()
