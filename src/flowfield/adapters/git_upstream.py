"""Refresh an adopted destination without discarding or rewriting local work."""

import hashlib
from pathlib import Path

from flowfield.adapters import git_checkout, git_integration
from flowfield.adapters.git_workspace import contains, git
from flowfield.errors import ApplicationError


def refresh(repository: Path, branch: str) -> str:
    """Caller owns the integration lock. Existing attempts retain their saved commits."""
    local = git_integration.target(repository, branch)
    remote = (
        git(repository, "config", "--get", f"branch.{branch}.remote", allowed_returncodes=(0, 1))
        .decode()
        .strip()
    )
    source = (
        git(repository, "config", "--get", f"branch.{branch}.merge", allowed_returncodes=(0, 1))
        .decode()
        .strip()
    )
    if not remote:
        # Newly configured local destinations remain local. A cloned branch already
        # has tracking configuration, so no remote or branch guessing is needed.
        return local
    if not source.startswith("refs/heads/"):
        raise ApplicationError(
            "upstream_invalid",
            "The destination has incomplete upstream configuration. Check its Git tracking branch.",
            409,
        )
    git(repository, "check-ref-format", source)
    if remote == ".":
        upstream = git_integration.resolve(repository, source)
    else:
        identity = hashlib.sha256(f"{remote}\0{source}".encode()).hexdigest()
        reference = f"refs/flowfield/upstreams/{identity}"
        try:
            git(
                repository,
                "-c",
                "maintenance.auto=false",
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "--no-write-fetch-head",
                "--",
                remote,
                f"+{source}:{reference}",
                input=b"",
                native_config=True,
            )
        except ApplicationError as error:
            if error.code == "git_cleanup_uncertain":
                raise
            raise ApplicationError(
                "upstream_unavailable",
                "Could not refresh the destination's upstream. Check repository access or "
                "connectivity; work will retry without using a stale baseline.",
                409,
            ) from error
        upstream = git_integration.resolve(repository, reference)
    if upstream == local or contains(repository, upstream, local):
        return local  # Already current, or contains local delivered work awaiting push.
    if not contains(repository, local, upstream):
        raise ApplicationError(
            "upstream_diverged",
            "The destination and its upstream have diverged. Reconcile their commits; "
            "Flowfield preserved both histories.",
            409,
        )
    git_checkout.deliver(repository, branch, local, upstream, git_checkout.binding(repository))
    return upstream
