"""Preview or publish an explicit Flowfield release."""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REPOSITORY = "flowfield-sh/flowfield-core"
ROOT = Path(__file__).resolve().parents[1]


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
    print(f"Release {current} -> {version}; push main and v{version} to {REPOSITORY}.", flush=True)
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


def publish(version: str, dry_run: bool) -> None:
    current = preflight(version)
    if dry_run:
        print(
            "Preview only: no changes, checks, tags or publication. "
            "Use make release-check to verify."
        )
        return
    run("gh", "auth", "status", "--hostname", "github.com")
    before = run("git", "rev-parse", "HEAD", capture=True)
    if version != current:
        run("uv", "version", version, "--no-sync")
    run("make", "setup", timeout=900)
    run("make", "release-check", timeout=1800)
    if run("git", "rev-parse", "HEAD", capture=True) != before:
        raise ValueError("HEAD changed during checks; inspect and rerun the release.")
    run("git", "diff", "--check")
    changed = run("git", "diff", "HEAD", "--name-only", capture=True).splitlines()
    if not set(changed) <= {"pyproject.toml", "uv.lock"}:
        raise ValueError("Unexpected changes during release checks; inspect the checkout.")
    if changed:
        run("git", "add", "--", *changed)
        run("git", "diff", "--cached", "--check")
        run("git", "diff", "--cached")
        run("git", "commit", "-m", f"Release {version}")
    # Recheck remote ancestry and unused tags after long-running checks, before publication.
    preflight(version)
    tag = f"v{version}"
    run("git", "tag", "-a", tag, "-m", f"Flowfield {version}")
    run("git", "push", "--atomic", "origin", "HEAD:refs/heads/main", f"refs/tags/{tag}")
    commit = run("git", "rev-parse", "HEAD", capture=True)
    for _ in range(30):
        runs = json.loads(
            run(
                "gh",
                "run",
                "list",
                "--repo",
                REPOSITORY,
                "--workflow",
                "workflow.yml",
                "--commit",
                commit,
                "--event",
                "push",
                "--json",
                "databaseId,headBranch,url",
                capture=True,
            )
        )
        matching = [item for item in runs if item["headBranch"] == tag]
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
            print(f"Published flowfield-core {version} and GitHub release {tag}.")
            return
        time.sleep(2)
    raise ValueError(
        "Tag pushed, but release run is not visible yet. Inspect Actions; do not retag."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="Explicit final version, e.g. 0.1.0")
    parser.add_argument("--dry-run", action="store_true", help="Read-only release preview")
    parser.add_argument(
        "--check-ci", action="store_true", help="Validate CI identity and extract notes"
    )
    args = parser.parse_args()
    if args.check_ci and (args.version or args.dry_run) or not args.check_ci and not args.version:
        parser.error("Choose VERSION [--dry-run] or --check-ci.")
    try:
        if args.check_ci:
            print(
                check_ci(
                    os.environ["GITHUB_EVENT_NAME"],
                    os.environ["GITHUB_REF"],
                    os.environ["GITHUB_REPOSITORY"],
                )
            )
        elif args.dry_run:
            publish(args.version, True)
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
                publish(args.version, False)
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
