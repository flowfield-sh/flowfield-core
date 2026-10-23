"""Release controls exercised with disposable Git repositories and no external publication."""

import importlib.util
import json
import subprocess
import zipfile
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
    (root / ".gitignore").write_text("release-notes.md\n.work/\n")
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
    "mode",
    [
        "preview",
        "publish",
        "next",
        "prepare",
        "resume",
        "failed_candidate",
        "failed_checks",
        "failed_push",
        "moved_head",
    ],
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
        if args[-1] == "--version" or args[:2] == ("uv", "publish"):
            return "fixture-tool-version"
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
            branch = "main" if "tests.yml" in args else f"v{version}"
            return json.dumps([{"databaseId": 1, "headBranch": branch, "url": "fixture"}])
        if args[0] == "gh":
            return ""
        return original(*args, capture=capture, timeout=timeout)

    monkeypatch.setattr(release, "run", run)
    identity = {
        "candidate_run": 1,
        "run_attempt": 1,
        "artifact_id": 2,
        "artifact_digest": "sha256:" + "a" * 64,
    }

    def candidate(*args):
        assert not git("tag", "--list", f"v{version}")
        assert git("rev-parse", "HEAD") == git("rev-parse", "main", cwd=remote)
        if mode == "failed_candidate":
            raise ValueError("Candidate did not pass")
        return identity

    monkeypatch.setattr(release, "fetch_candidate", candidate)
    if mode in {"failed_checks", "failed_push", "moved_head", "failed_candidate"}:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            release.publish(version, False)
    else:
        release.publish(
            version, mode == "preview", mode == "prepare", 1 if mode == "resume" else None
        )
    remote_tags = git("tag", "--list", cwd=remote).splitlines()
    if mode in {"publish", "next", "resume"}:
        assert f"v{version}" in remote_tags
        assert git("rev-parse", "refs/heads/main", cwd=remote) == git(
            "rev-parse", f"v{version}^{{}}"
        )
        assert (
            json.loads(git("for-each-ref", "--format=%(contents)", f"refs/tags/v{version}"))
            == identity
        )
        if mode == "resume":
            assert not any(call[0] in {"make", "uv"} for call in calls)
        else:
            tag_index = next(i for i, call in enumerate(calls) if call[:3] == ("git", "tag", "-a"))
            watch_index = next(
                i for i, call in enumerate(calls) if call[:3] == ("gh", "run", "watch")
            )
            assert watch_index < tag_index
        assert git("status", "--porcelain") == ""
        if mode == "next":
            assert set(git("diff", "--name-only", initial, "HEAD").splitlines()) == {
                "pyproject.toml",
                "uv.lock",
            }
    else:
        assert remote_tags == []
        assert git("tag", "--list").splitlines() == []
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


@pytest.fixture
def candidate(repository, release, monkeypatch, tmp_path):
    root, _, git = repository
    for name in (*release.INPUTS, *release.FIXTURES):
        file = root / name
        if not file.exists():
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(f"Fixture {name}\n")
    git("add", ".")
    git("commit", "-m", "Candidate inputs")
    git("push", "origin", "main")
    commit = git("rev-parse", "HEAD")
    (root / "dist").mkdir()
    (root / "dist" / ".gitignore").write_text("*")  # uv build's directory marker
    for name in release.distributions("0.1.0"):
        (root / "dist" / name).write_bytes(name.encode())
    monkeypatch.setenv("GITHUB_REPOSITORY", release.REPOSITORY)
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    directory = tmp_path / "candidate"
    release.build_candidate(directory)
    return directory, commit


def test_candidate_binds_source_notes_inputs_and_every_original_file(candidate, release):
    directory, commit = candidate
    receipt = release.verify_payload(directory, commit, 7, 1)
    assert receipt["version"] == "0.1.0"
    assert len(receipt["files"]) == 10
    with pytest.raises(ValueError, match="source/version/run"):
        release.verify_payload(directory, "a" * 40, 7, 1)
    with pytest.raises(ValueError, match="source/version/run"):
        release.verify_payload(directory, commit, 7, 2)
    (release.ROOT / "uv.lock").write_text(
        (release.ROOT / "uv.lock").read_text() + "\n# Changed dependency resolution\n"
    )
    with pytest.raises(ValueError, match="inputs differ"):
        release.verify_payload(directory, commit, 7, 1)


@pytest.mark.parametrize("change", ["bytes", "extra", "missing", "symlink", "notes", "sums"])
def test_candidate_rejects_tampering(candidate, release, change):
    directory, commit = candidate
    payload = next((directory / "dist").glob("*.whl"))
    if change == "bytes":
        payload.write_bytes(b"different build")
    elif change == "extra":
        (directory / "dist/extra.whl").touch()
    elif change == "missing":
        payload.unlink()
    elif change == "symlink":
        payload.unlink()
        payload.symlink_to(directory / "release-notes.md")
    else:
        name = "release-notes.md" if change == "notes" else "SHA256SUMS"
        (directory / name).write_text("unreviewed\n")
        receipt = json.loads((directory / "candidate.json").read_text())
        receipt["files"][name] = release.digest(directory / name)
        (directory / "candidate.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        release.verify_payload(directory, commit, 7, 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("head_branch", "feature"),
        ("head_sha", "b" * 40),
        ("path", ".github/workflows/untrusted.yml"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("id", 8),
        ("run_attempt", 2),
        ("repository", {"full_name": "other/fork"}),
        ("head_repository", {"full_name": "other/fork"}),
    ],
)
def test_promotion_rejects_unverified_run_identity(release, field, value):
    run = {
        "id": 7,
        "head_sha": "a" * 40,
        "head_branch": "main",
        "path": release.WORKFLOW,
        "event": "push",
        "repository": {"full_name": release.REPOSITORY},
        "head_repository": {"full_name": release.REPOSITORY},
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
    }
    release.validate_run(run, "a" * 40, 7, 1)
    run[field] = value
    with pytest.raises(ValueError, match="successful Tests"):
        release.validate_run(run, "a" * 40, 7, 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("expired", True),
        ("name", "release-candidate-2"),
        ("digest", "bad"),
        ("workflow_run", {"id": 8, "head_sha": "a" * 40}),
        ("workflow_run", {"id": 7, "head_sha": "b" * 40}),
        ("size_in_bytes", 0),
    ],
)
def test_promotion_rejects_expired_or_wrong_artifacts(release, field, value):
    artifact = {
        "id": 11,
        "expired": False,
        "name": "release-candidate-1",
        "digest": "sha256:" + "c" * 64,
        "size_in_bytes": 100,
        "workflow_run": {"id": 7, "head_sha": "a" * 40},
    }
    release.validate_artifact(artifact, "a" * 40, 7, 1)
    artifact[field] = value
    with pytest.raises(ValueError, match="artifact is expired"):
        release.validate_artifact(artifact, "a" * 40, 7, 1)


def test_fetch_original_candidate_and_pinned_attempt(candidate, release, monkeypatch, tmp_path):
    directory, commit = candidate
    archive = tmp_path / "original.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for file in directory.rglob("*"):
            if file.is_file():
                zipped.write(file, file.relative_to(directory))
    workflow = {
        "id": 7,
        "head_sha": commit,
        "head_branch": "main",
        "path": release.WORKFLOW,
        "event": "push",
        "repository": {"full_name": release.REPOSITORY},
        "head_repository": {"full_name": release.REPOSITORY},
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
    }
    artifact = {
        "id": 11,
        "expired": False,
        "name": "release-candidate-1",
        "digest": "sha256:" + release.digest(archive),
        "size_in_bytes": archive.stat().st_size,
        "workflow_run": {"id": 7, "head_sha": commit},
    }
    seen = []

    def api(path):
        seen.append(path)
        if path.endswith("artifacts?per_page=100"):
            return {"artifacts": [artifact]}
        return artifact if path == "actions/artifacts/11" else workflow

    original = subprocess.run

    def download(args, **kwargs):
        if args[0] != "gh":
            return original(args, **kwargs)
        kwargs["stdout"].write(archive.read_bytes())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(release, "api", api)
    monkeypatch.setattr(release.subprocess, "run", download)
    pin = release.fetch_candidate(7, tmp_path / "download")
    assert pin["artifact_id"] == 11
    assert release.promotion_identity(json.dumps(pin)) == pin
    assert release.fetch_candidate(7, tmp_path / "retry", pin) == pin
    assert "actions/runs/7/attempts/1" in seen
    assert (tmp_path / "retry/SHA256SUMS").read_bytes() == (directory / "SHA256SUMS").read_bytes()
    with pytest.raises(ValueError, match="pinned identity"):
        release.fetch_candidate(
            7, tmp_path / "wrong", {**pin, "artifact_digest": "sha256:" + "0" * 64}
        )
    artifact["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="archive digest"):
        release.fetch_candidate(7, tmp_path / "corrupt")


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "nested/../../escape", "nested\\escape"]
)
def test_candidate_archive_rejects_unsafe_paths(release, tmp_path, name):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(name, b"untrusted")
    with pytest.raises(ValueError, match="unsafe path"):
        release.extract_candidate(archive, tmp_path / "output")
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("message", ["Flowfield 0.4.0", "{}", "[]", '{"candidate_run":"7"}'])
def test_unpinned_tags_cannot_publish(release, message):
    with pytest.raises(ValueError, match="tag must pin"):
        release.promotion_identity(message)


def test_ci_promotion_requires_annotated_pin_and_rehearsal_never_tags(
    repository, release, monkeypatch
):
    _, _, git = repository
    pin = {
        "candidate_run": 7,
        "run_attempt": 1,
        "artifact_id": 11,
        "artifact_digest": "sha256:" + "a" * 64,
    }
    calls = []
    monkeypatch.setattr(release, "fetch_candidate", lambda *args: calls.append(args) or pin)
    monkeypatch.setenv("GITHUB_REPOSITORY", release.REPOSITORY)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    release.promote_ci(Path("unused"), 7)
    assert calls == [(7, Path("unused"), None)]
    assert git("tag", "--list") == ""
    with pytest.raises(ValueError, match="needs --candidate-run"):
        release.promote_ci(Path("unused"), None)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v0.1.0")
    git("tag", "v0.1.0")
    with pytest.raises(ValueError, match="annotated"):
        release.promote_ci(Path("unused"), 7)
    git("tag", "-f", "-a", "v0.1.0", "-m", json.dumps(pin))
    release.promote_ci(Path("unused"), 999)
    assert calls[-1] == (7, Path("unused"), pin)


def test_expired_candidate_never_tags_or_rebuilds(repository, release, monkeypatch):
    _, _, git = repository
    original = release.run
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("git", "remote", "get-url"):
            return f"git@github.com:{release.REPOSITORY}.git"
        if args[0] == "gh":
            return ""
        return original(*args, **kwargs)

    def expired(*args):
        raise ValueError("Candidate artifact is expired")

    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "fetch_candidate", expired)
    with pytest.raises(ValueError, match="expired"):
        release.publish("0.1.0", False, candidate_run=7)
    assert not git("tag", "--list")
    assert not any(call[0] in {"make", "uv"} or call[:2] == ("git", "push") for call in calls)


def test_local_stages_resume_and_invalidate_only_relevant_success(repository, release, monkeypatch):
    root, _, git = repository
    for name in ("web/e2e/example.spec.ts", "src/app.py", "tests/test_app.py"):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("original\n")
    git("add", ".")
    git("commit", "-m", "Stage inputs")
    original = release.run
    calls = []
    fail = True

    def run(*args, **kwargs):
        nonlocal fail
        if args[0] == "git":
            return original(*args, **kwargs)
        if args[-1] == "--version":
            return "fixed-tool"
        calls.append(args)
        if args == ("make", "smoke") and fail:
            fail = False
            raise subprocess.CalledProcessError(1, args)
        if args == ("make", "build"):
            (root / "dist").mkdir(exist_ok=True)
            for name in release.distributions("0.1.0"):
                (root / "dist" / name).write_text("original artifact")
        return ""

    monkeypatch.setattr(release, "run", run)
    monkeypatch.delenv("FLOWFIELD_BUILD_BUN", raising=False)
    with pytest.raises(subprocess.CalledProcessError):
        release.preparation_stages("0.1.0")
    calls.clear()
    release.preparation_stages("0.1.0")
    assert ("make", "setup") in calls
    assert ("make", "quality") not in calls and ("make", "test") not in calls
    assert ("make", "smoke") in calls and ("make", "build") in calls
    calls.clear()
    release.preparation_stages("0.1.0")
    assert calls == [("make", "setup")]
    (root / "web/e2e/example.spec.ts").write_text("fixed assertion\n")
    calls.clear()
    release.preparation_stages("0.1.0")
    assert ("make", "test") not in calls
    assert ("make", "smoke") in calls and ("make", "build") in calls
    # Generated package deletion/tampering invalidates build and installed evidence.
    next((root / "dist").glob("*.whl")).unlink()
    calls.clear()
    release.preparation_stages("0.1.0")
    assert ("make", "build") in calls
    (root / "src/app.py").write_text("changed behavior\n")
    calls.clear()
    release.preparation_stages("0.1.0")
    assert ("make", "test") in calls and ("make", "smoke") in calls


def test_stage_success_is_not_recorded_when_source_changes(repository, release, monkeypatch):
    root, _, _ = repository
    original = release.run

    def run(*args, **kwargs):
        if args[0] == "git":
            return original(*args, **kwargs)
        if args == ("make", "quality"):
            (root / "CHANGELOG.md").write_text("concurrent edit")
        return "tool"

    monkeypatch.setattr(release, "run", run)
    monkeypatch.delenv("FLOWFIELD_BUILD_BUN", raising=False)
    with pytest.raises(ValueError, match="Inputs changed"):
        release.preparation_stages("0.1.0")
    assert not json.loads((root / ".work/release/preparation.json").read_text())["stages"]


def test_public_verification_retries_reads_but_rejects_changed_bytes(monkeypatch):
    verifier = load("verify_public")
    calls = []

    def read(url, wait=120):
        calls.append(url)
        return b"old" if len(calls) == 1 else b"published"

    monkeypatch.setattr(verifier, "read", read)
    monkeypatch.setattr(verifier.time, "sleep", lambda _: None)
    verifier.matching("https://fixture.invalid/asset", verifier.digest(b"published"), wait=1)
    assert len(calls) == 2
    with pytest.raises(ValueError, match="Public content differs"):
        verifier.matching("https://fixture.invalid/asset", verifier.digest(b"different"), wait=0)
    assert verifier.normalized("Use [Codex](/agents/codex).") == verifier.normalized(
        "Use [Codex](https://docs.flowfield.sh/agents/codex.md)."
    )


def test_public_verification_bounds_transient_registry_wait(monkeypatch):
    verifier = load("verify_public")
    from io import BytesIO
    from urllib.error import HTTPError

    calls = []

    def open_url(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 404, "Propagating", {}, None)
        return BytesIO(b"published")

    monkeypatch.setattr(verifier, "urlopen", open_url)
    monkeypatch.setattr(verifier.time, "sleep", lambda _: None)
    assert verifier.read("https://fixture.invalid/index", wait=1) == b"published"
    assert len(calls) == 2
    calls.clear()
    with pytest.raises(HTTPError):
        verifier.read("https://fixture.invalid/index", wait=0)
    assert len(calls) == 1
