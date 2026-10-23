"""Preview or publish an explicit Flowfield release."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

REPOSITORY = "flowfield-sh/flowfield-core"
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/tests.yml"
INPUTS = (
    "pyproject.toml",
    "uv.lock",
    "web/pnpm-lock.yaml",
    "docs/pnpm-lock.yaml",
    "runtimes/claude-sdk/pnpm-lock.yaml",
)
FIXTURES = (
    "scripts/check_dist.py",
    "scripts/check-install.py",
    "tests/fake_claude_cli.py",
    "tests/fake_pi.py",
)


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def distributions(version: str) -> set[str]:
    version_tuple(version)
    return {f"flowfield_core-{version}.tar.gz"} | {
        f"flowfield_core-{version}-py3-none-{platform}.whl"
        for platform in ("macosx_13_0_arm64", "manylinux_2_28_aarch64", "manylinux_2_28_x86_64")
    }


def build_candidate(destination: Path) -> None:
    """Record the single CI build before the original-artifact installation matrix."""
    version = current_version()
    check_lock(version)
    run("git", "diff", "--exit-code", "HEAD", "--", ".")
    files = sorted(file for file in (ROOT / "dist").iterdir() if file.name != ".gitignore")
    if {file.name for file in files} != distributions(version):
        raise ValueError("Candidate needs exactly the three platform wheels and source archive.")
    destination.mkdir(parents=True, exist_ok=False)
    for source in [*(f"dist/{file.name}" for file in files), *FIXTURES]:
        target = destination / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
    (destination / "release-notes.md").write_text(
        release_notes((ROOT / "CHANGELOG.md").read_text(), version)
    )
    (destination / "SHA256SUMS").write_text(
        "".join(f"{digest(file)}  ./{file.name}\n" for file in files)
    )
    receipt = {
        "schema": 1,
        "repository": os.environ["GITHUB_REPOSITORY"],
        "commit": run("git", "rev-parse", "HEAD", capture=True),
        "version": version,
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "inputs": {name: digest(ROOT / name) for name in INPUTS},
        "files": {
            file.relative_to(destination).as_posix(): digest(file)
            for file in sorted(destination.rglob("*"))
            if file.is_file()
        },
    }
    (destination / "candidate.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as file:
            file.write(f"version={version}\n")


def api(path: str):
    return json.loads(run("gh", "api", f"repos/{REPOSITORY}/{path}", capture=True))


def validate_run(value: dict, commit: str, run_id: int, attempt: int | None = None) -> None:
    if (
        value.get("id") != run_id
        or value.get("head_sha") != commit
        or value.get("head_branch") != "main"
        or value.get("path") != WORKFLOW
        or value.get("event") not in {"push", "workflow_dispatch"}
        or value.get("repository", {}).get("full_name") != REPOSITORY
        or value.get("head_repository", {}).get("full_name") != REPOSITORY
        or value.get("status") != "completed"
        or value.get("conclusion") != "success"
        or (attempt is not None and value.get("run_attempt") != attempt)
    ):
        raise ValueError("Candidate must be a successful Tests run on this exact main commit.")


def validate_artifact(value: dict, commit: str, run_id: int, attempt: int) -> None:
    if (
        value.get("expired") is not False
        or value.get("name") != f"release-candidate-{attempt}"
        or value.get("workflow_run", {}).get("id") != run_id
        or value.get("workflow_run", {}).get("head_sha") != commit
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("digest", ""))
        or not 0 < value.get("size_in_bytes", 0) <= 512 * 1024 * 1024
    ):
        raise ValueError("Candidate artifact is expired, missing, or has a different identity.")


def verify_payload(directory: Path, commit: str, run_id: int, attempt: int) -> dict:
    receipt = json.loads((directory / "candidate.json").read_text())
    version = current_version()
    check_lock(version)
    identity = {
        "schema": 1,
        "repository": REPOSITORY,
        "commit": commit,
        "version": version,
        "run_id": run_id,
        "run_attempt": attempt,
    }
    if any(receipt.get(key) != value for key, value in identity.items()):
        raise ValueError("Candidate receipt does not identify this source/version/run attempt.")
    if receipt.get("inputs") != {name: digest(ROOT / name) for name in INPUTS}:
        raise ValueError("Candidate build inputs differ from the checked-out source.")
    expected = {
        *(f"dist/{name}" for name in distributions(version)),
        *FIXTURES,
        "release-notes.md",
        "SHA256SUMS",
    }
    files = {
        file.relative_to(directory).as_posix() for file in directory.rglob("*") if file.is_file()
    }
    if (
        files != expected | {"candidate.json"}
        or set(receipt.get("files", {})) != expected
        or any(file.is_symlink() for file in directory.rglob("*"))
    ):
        raise ValueError("Candidate payload contains missing or unexpected files.")
    if any(digest(directory / name) != checksum for name, checksum in receipt["files"].items()):
        raise ValueError("Candidate payload checksum mismatch.")
    sums = "".join(
        f"{digest(directory / 'dist' / name)}  ./{name}\n"
        for name in sorted(distributions(version))
    )
    if (directory / "SHA256SUMS").read_text() != sums:
        raise ValueError("Distribution checksums do not match the candidate.")
    if (directory / "release-notes.md").read_text() != release_notes(
        (ROOT / "CHANGELOG.md").read_text(), version
    ):
        raise ValueError("Candidate release notes differ from reviewed source.")
    return receipt


def extract_candidate(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as files:
        names = files.namelist()
        if (
            len(names) > 32
            or len(names) != len(set(names))
            or sum(item.file_size for item in files.infolist()) > 512 * 1024 * 1024
        ):
            raise ValueError("Candidate archive exceeds its bounds or repeats paths.")
        for item in files.infolist():
            path = Path(item.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in item.filename
                or (item.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("Candidate archive contains an unsafe path.")
        files.extractall(destination)


def fetch_candidate(run_id: int, destination: Path, pin: dict | None = None) -> dict:
    """Download an immutable candidate; only a completed full verification can promote it."""
    if run_id <= 0:
        raise ValueError("Candidate run ID must be positive.")
    commit = run("git", "rev-parse", "HEAD", capture=True)
    run("git", "merge-base", "--is-ancestor", "HEAD", "origin/main")
    endpoint = f"actions/runs/{run_id}"
    value = api(endpoint + (f"/attempts/{pin['run_attempt']}" if pin else ""))
    validate_run(value, commit, run_id, pin["run_attempt"] if pin else None)
    attempt = value["run_attempt"]
    if pin:
        artifact = api(f"actions/artifacts/{pin['artifact_id']}")
    else:
        artifacts = api(endpoint + "/artifacts?per_page=100")["artifacts"]
        matches = [item for item in artifacts if item["name"] == f"release-candidate-{attempt}"]
        if len(matches) != 1:
            raise ValueError("No unique candidate for this attempt; rerun all Tests jobs on main.")
        artifact = matches[0]
    validate_artifact(artifact, commit, run_id, attempt)
    identity = {
        "candidate_run": run_id,
        "run_attempt": attempt,
        "artifact_id": artifact["id"],
        "artifact_digest": artifact["digest"],
    }
    if pin is not None and pin != identity:
        raise ValueError("Candidate artifact differs from the annotated tag's pinned identity.")
    cache = ROOT / ".work" / "release" / "archives"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"{artifact['id']}.zip"
    if not archive.exists():
        # Interrupted downloads never become reusable artifacts.
        with tempfile.TemporaryDirectory(prefix="download-", dir=cache) as scratch:
            temporary = Path(scratch) / "candidate.zip"
            with temporary.open("wb") as output:
                subprocess.run(
                    [
                        "gh",
                        "api",
                        "--allow-escape-sequences",
                        f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip",
                    ],
                    cwd=ROOT,
                    stdout=output,
                    check=True,
                    timeout=180,
                )
            if f"sha256:{digest(temporary)}" != artifact["digest"]:
                raise ValueError("Downloaded candidate archive digest mismatch.")
            temporary.replace(archive)
    if f"sha256:{digest(archive)}" != artifact["digest"]:
        raise ValueError("Cached candidate archive digest mismatch; remove the corrupt cache.")
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="extract-", dir=destination.parent) as scratch:
            extracted = Path(scratch) / "candidate"
            extract_candidate(archive, extracted)
            verify_payload(extracted, commit, run_id, attempt)
            extracted.rename(destination)
    verify_payload(destination, commit, run_id, attempt)
    # These are reproducible downloads, not workspace evidence. Bound local disk use.
    for directory, current, suffix in [
        (cache, archive, ".zip"),
        (ROOT / ".work" / "release" / "candidates", destination, ""),
    ]:
        entries = sorted(
            (
                path
                for path in directory.glob("*")
                if not path.is_symlink() and path.name.removesuffix(suffix).isdigit()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        others = [path for path in entries if path != current]
        for path in others[2:]:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    return identity


def promotion_identity(message: str) -> dict:
    try:
        value = json.loads(message)
        if (
            set(value) != {"candidate_run", "run_attempt", "artifact_id", "artifact_digest"}
            or any(
                type(value[key]) is not int or value[key] <= 0
                for key in ("candidate_run", "run_attempt", "artifact_id")
            )
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["artifact_digest"])
        ):
            raise ValueError
        return value
    except (ValueError, TypeError) as error:
        raise ValueError(
            "Release tag must pin a verified candidate run, attempt and artifact."
        ) from error


def promote_ci(destination: Path, candidate_run: int | None) -> None:
    event, ref = os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"]
    check_ci(event, ref, os.environ["GITHUB_REPOSITORY"])
    pin = None
    if event == "push":
        if run("git", "cat-file", "-t", ref, capture=True) != "tag":
            raise ValueError("Publication requires an annotated candidate tag.")
        pin = promotion_identity(
            run("git", "for-each-ref", "--format=%(contents)", ref, capture=True)
        )
        candidate_run = pin["candidate_run"]
    if candidate_run is None:
        raise ValueError("A promotion rehearsal needs --candidate-run.")
    identity = fetch_candidate(candidate_run, destination, pin)
    print(f"Verified candidate promotion: {json.dumps(identity, sort_keys=True)}", flush=True)


def run(*args: str, capture: bool = False, timeout: int = 120) -> str:
    result = subprocess.run(
        args, cwd=ROOT, check=True, text=True, capture_output=capture, timeout=timeout
    )
    return result.stdout.strip() if capture else ""


def version_tuple(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value):
        raise ValueError("Use a final version such as 0.1.0, without a v prefix or suffix.")
    return tuple(map(int, value.split(".")))


def release_notes(changelog: str, version: str) -> str:
    version_tuple(version)
    sections = re.findall(
        rf"^## {re.escape(version)}\n(.*?)(?=^## |\Z)", changelog, re.MULTILINE | re.DOTALL
    )
    if len(sections) != 1 or not sections[0].strip():
        raise ValueError(f"CHANGELOG.md needs exactly one nonempty '## {version}' section.")
    return sections[0].strip() + "\n"


def check_version(version: str, current: str, tags: list[str]) -> None:
    requested = version_tuple(version)
    released = [version_tuple(tag[1:]) for tag in tags if re.fullmatch(r"v\d+\.\d+\.\d+", tag)]
    if requested < version_tuple(current) or any(requested <= item for item in released):
        raise ValueError("Version must not decrease and must exceed every existing release tag.")
    if not released and version != "0.1.0":
        raise ValueError("The first release must be 0.1.0.")


def current_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def check_lock(version: str) -> None:
    packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    versions = [package["version"] for package in packages if package["name"] == "flowfield-core"]
    if versions != [version]:
        raise ValueError("pyproject.toml and uv.lock must identify the same Flowfield version.")


def preflight(version: str) -> str:
    version_tuple(version)
    if run("git", "branch", "--show-current", capture=True) != "main":
        raise ValueError("Run releases from the clean, integrated main checkout.")
    if run("git", "status", "--porcelain", capture=True):
        raise ValueError("Commit reviewed notes and other changes before releasing.")
    allowed = {f"git@github.com:{REPOSITORY}.git", f"https://github.com/{REPOSITORY}.git"}
    for args in [
        ("remote", "get-url", "origin"),
        ("remote", "get-url", "--push", "--all", "origin"),
    ]:
        if run("git", *args, capture=True) not in allowed:
            raise ValueError(f"origin must fetch and push only github.com/{REPOSITORY}.git.")
    remote = run("git", "ls-remote", "origin", "refs/heads/main", "refs/tags/v*", capture=True)
    refs = dict(line.split()[::-1] for line in remote.splitlines())
    remote_main = refs.get("refs/heads/main")
    if not remote_main:
        raise ValueError("origin has no main branch.")
    run("git", "merge-base", "--is-ancestor", remote_main, "HEAD")
    tags = run("git", "tag", "--list", "v*", capture=True).splitlines()
    tags += [ref.removeprefix("refs/tags/") for ref in refs if ref.startswith("refs/tags/")]
    current = current_version()
    check_lock(current)
    check_version(version, current, tags)
    print(
        f"Release {current} -> {version}; verify main before tagging in {REPOSITORY}.", flush=True
    )
    print("Outgoing commits:", flush=True)
    run("git", "log", "--oneline", f"{remote_main}..HEAD")
    print(release_notes((ROOT / "CHANGELOG.md").read_text(), version), flush=True)
    return current


def check_ci(event: str, ref: str, repository: str) -> str:
    version = current_version()
    version_tuple(version)
    check_lock(version)
    if repository != REPOSITORY:
        raise ValueError("Release workflow must run in the configured public repository.")
    if (event, ref) != ("workflow_dispatch", "refs/heads/main") and (
        event != "push" or ref != f"refs/tags/v{version}"
    ):
        raise ValueError("Use a matching vX.Y.Z tag, or manually rehearse on main.")
    run("git", "merge-base", "--is-ancestor", "HEAD", "origin/main")
    tags = run("git", "tag", "--list", "v*", capture=True).splitlines()
    check_version(version, version, [tag for tag in tags if tag != f"v{version}"])
    if event == "push" and run("git", "rev-parse", f"{ref}^{{}}", capture=True) != run(
        "git", "rev-parse", "HEAD", capture=True
    ):
        raise ValueError("The release tag must identify the checked-out source.")
    notes = release_notes((ROOT / "CHANGELOG.md").read_text(), version)
    (ROOT / "release-notes.md").write_text(notes)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as file:
            file.write(f"version={version}\n")
    return version


def wait_run(workflow: str, commit: str, branch: str) -> int:
    for _ in range(30):
        runs = json.loads(
            run(
                "gh",
                "run",
                "list",
                "--repo",
                REPOSITORY,
                "--workflow",
                workflow,
                "--commit",
                commit,
                "--event",
                "push",
                "--json",
                "databaseId,headBranch,url",
                capture=True,
            )
        )
        matching = [item for item in runs if item["headBranch"] == branch]
        if matching:
            print(matching[0]["url"], flush=True)
            run(
                "gh",
                "run",
                "watch",
                str(matching[0]["databaseId"]),
                "--repo",
                REPOSITORY,
                "--exit-status",
                timeout=2400,
            )
            return matching[0]["databaseId"]
        time.sleep(2)
    raise ValueError(
        "Pushed source is preserved, but its workflow is not visible. Inspect Actions."
    )


def stage_fingerprint(stage: str, command: tuple[str, ...], environment: dict) -> str:
    """Exclude only inputs that the named check cannot consume; packages include all source."""
    files = run("git", "ls-files", "-z", capture=True).split("\0")
    hashes = {}
    for name in filter(None, files):
        if stage == "backend" and name.startswith(("web/", "docs/", ".github/")):
            continue
        if stage == "browser" and name.startswith("tests/test_"):
            continue
        hashes[name] = digest(ROOT / name)
    value = {"files": hashes, "command": command, "environment": environment}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def preparation_stages(version: str) -> None:
    """Resume successful local stages, without replacing exact-source hosted verification."""
    directory = ROOT / ".work" / "release"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "preparation.json"
    receipt = json.loads(path.read_text()) if path.exists() else {"schema": 1, "stages": {}}
    if receipt.get("schema") != 1:
        raise ValueError("Unknown local preparation receipt; inspect it before retrying.")

    def save() -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt, indent=2) + "\n")
        temporary.replace(path)

    environment = {
        "checkout": str(ROOT),
        "python": sys.version,
        "executable": sys.executable,
        "platform": sys.platform,
        "path": os.environ.get("PATH", ""),
        "bun": os.environ.get("FLOWFIELD_BUILD_BUN", ""),
        "tools": {tool: run(tool, "--version", capture=True) for tool in ("uv", "node", "pnpm")},
    }
    if environment["bun"]:
        environment["bun_digest"] = digest(Path(environment["bun"]))
    # Locked dependencies and host runtime must exist even after local cache cleanup.
    run("make", "setup", timeout=900)
    stages = [
        ("quality", ("make", "quality")),
        ("backend", ("make", "test")),
        ("browser", ("make", "smoke")),
        ("build", ("make", "build")),
        ("installed", ("make", "check-dist")),
        (
            "metadata",
            (
                "uv",
                "publish",
                "--dry-run",
                "--trusted-publishing",
                "never",
                *(str(ROOT / "dist" / name) for name in sorted(distributions(version))),
            ),
        ),
    ]
    for name, command in stages:
        key = stage_fingerprint(name, command, environment)
        saved = receipt["stages"].get(name, {})
        packages = {
            str(file.relative_to(ROOT)): digest(file)
            for file in (ROOT / "dist").glob("*")
            if file.is_file() and file.suffix in {".whl", ".gz"}
        }
        has_packages = name in {"build", "installed", "metadata"}
        if saved.get("fingerprint") == key and (
            not has_packages or (packages and saved.get("packages") == packages)
        ):
            print(f"Reuse local {name}: verified inputs unchanged ({saved['source']}).", flush=True)
            continue
        # A failed/restarted stage cannot leave an older success reusable.
        receipt["stages"].pop(name, None)
        save()
        started = time.monotonic()
        print(f"Run local {name}.", flush=True)
        run(*command, timeout=1800)
        if key != stage_fingerprint(name, command, environment):
            raise ValueError(f"Inputs changed during {name}; no success recorded.")
        receipt["stages"][name] = {
            "fingerprint": key,
            "source": run("git", "rev-parse", "HEAD", capture=True),
            "command": command,
            "seconds": round(time.monotonic() - started, 3),
            "packages": {
                str(file.relative_to(ROOT)): digest(file)
                for file in (ROOT / "dist").glob("*")
                if file.is_file() and file.suffix in {".whl", ".gz"}
            }
            if has_packages
            else {},
        }
        save()


def publish(
    version: str, dry_run: bool, prepare: bool = False, candidate_run: int | None = None
) -> None:
    current = preflight(version)
    if dry_run:
        print(
            "Preview only: no changes, checks, tags or publication. "
            "The helper prepares and verifies a candidate before creating a tag."
        )
        return
    run("gh", "auth", "status", "--hostname", "github.com")
    if candidate_run is None:
        if version != current:
            run("uv", "version", version, "--no-sync")
            run("git", "diff", "--check")
            changed = run("git", "diff", "HEAD", "--name-only", capture=True).splitlines()
            if not set(changed) <= {"pyproject.toml", "uv.lock"}:
                raise ValueError("Unexpected changes while setting the version.")
            run("git", "add", "--", "pyproject.toml", "uv.lock")
            run("git", "commit", "-m", f"Prepare release {version}")
        # Commit version metadata first: failures leave a clean, resumable source.
        before = run("git", "rev-parse", "HEAD", capture=True)
        preparation_stages(version)
        if run("git", "rev-parse", "HEAD", capture=True) != before:
            raise ValueError("HEAD changed during checks; inspect and rerun the release.")
        preflight(version)
        run("git", "push", "origin", "HEAD:refs/heads/main")
        commit = run("git", "rev-parse", "HEAD", capture=True)
        candidate_run = wait_run("tests.yml", commit, "main")
    elif current != version:
        raise ValueError("Candidate promotion cannot change the version or source.")
    commit = run("git", "rev-parse", "HEAD", capture=True)
    identity = fetch_candidate(
        candidate_run, ROOT / ".work" / "release" / "candidates" / str(candidate_run)
    )
    if prepare:
        print(
            f"Verified candidate {candidate_run} for {version} at {commit}; no tag or publication."
        )
        print(f"Publish with scripts/release.py {version} --candidate-run {candidate_run}")
        return
    # Recheck remote ancestry and unused tags after verification, before publication.
    preflight(version)
    tag = f"v{version}"
    if run("git", "rev-parse", "HEAD", capture=True) != commit:
        raise ValueError("HEAD changed during candidate verification.")
    run("git", "tag", "-a", tag, commit, "-m", json.dumps(identity, sort_keys=True))
    run("git", "push", "origin", f"refs/tags/{tag}")
    wait_run("workflow.yml", commit, tag)
    print(f"Published flowfield-core {version} and GitHub release {tag}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="Explicit final version, e.g. 0.1.0")
    parser.add_argument("--dry-run", action="store_true", help="Read-only release preview")
    parser.add_argument("--prepare", action="store_true", help="Verify a candidate without tagging")
    parser.add_argument("--candidate-run", type=int, help="Promote this successful Tests run")
    parser.add_argument("--build-candidate", type=Path, help="CI: record built candidate files")
    parser.add_argument(
        "--promote-ci", type=Path, help="CI: verify/download candidate for promotion"
    )
    parser.add_argument(
        "--check-ci", action="store_true", help="Validate CI identity and extract notes"
    )
    args = parser.parse_args()
    modes = sum(bool(value) for value in (args.check_ci, args.build_candidate, args.promote_ci))
    if (
        modes > 1
        or (modes and (args.version or args.dry_run or args.prepare))
        or (not modes and not args.version)
        or (args.candidate_run is not None and args.candidate_run <= 0)
        or (args.candidate_run is not None and (args.build_candidate or args.check_ci))
    ):
        parser.error("Choose VERSION [--prepare] [--candidate-run ID] [--dry-run], or one CI mode.")
    try:
        if args.build_candidate:
            build_candidate(args.build_candidate)
        elif args.promote_ci:
            promote_ci(args.promote_ci, args.candidate_run)
        elif args.check_ci:
            print(
                check_ci(
                    os.environ["GITHUB_EVENT_NAME"],
                    os.environ["GITHUB_REF"],
                    os.environ["GITHUB_REPOSITORY"],
                )
            )
        elif args.dry_run:
            publish(args.version, True, args.prepare, args.candidate_run)
        else:
            common = run(
                "git", "rev-parse", "--path-format=absolute", "--git-common-dir", capture=True
            )
            with (Path(common) / "flowfield-release.lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    print("Another release is running; retry after it finishes.", file=sys.stderr)
                    return 75
                publish(args.version, False, args.prepare, args.candidate_run)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"Release stopped: {error}", file=sys.stderr)
        print(
            "State is preserved. Inspect completed steps before retrying; do not move tags.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
