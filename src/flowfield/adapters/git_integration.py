"""Local Git integration: isolated candidates, bounded checks, compare-and-swap refs."""

import asyncio
import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from flowfield.adapters import git_checkout, local_checks
from flowfield.adapters.git_workspace import contains, git
from flowfield.adapters.local_execution import LocalAttempt
from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult
from flowfield.integration_models import CheckoutBinding


@lru_cache(maxsize=256)
def branch_ref(repository: Path, branch: str) -> str:
    # Cache successful syntax validation only. Targets, checkout identity and
    # authority are mutable and must still be observed on every operation.
    # Full refs, option-like input and reflog expressions are not branch identities.
    if branch.startswith(("-", "refs/")) or "@{" in branch:
        raise ApplicationError("invalid_target", "Choose a local branch name, such as integration.")
    git(repository, "check-ref-format", "refs/heads/" + branch)
    return "refs/heads/" + branch


def resolve(repository: Path, reference: str) -> str:
    return (
        git(repository, "rev-parse", "--verify", "--end-of-options", reference + "^{commit}")
        .decode()
        .strip()
    )


def target(repository: Path, branch: str) -> str:
    commit = (
        git(
            repository,
            "rev-parse",
            "--quiet",
            "--verify",
            "--end-of-options",
            branch_ref(repository, branch) + "^{commit}",
            allowed_returncodes=(0, 1),
        )
        .decode()
        .strip()
    )
    if not commit:
        raise ApplicationError(
            "missing_destination",
            f"Destination branch '{branch}' does not exist. Save it in integration settings "
            "to create it from the project's current commit, or choose an existing branch.",
        )
    return commit


@contextmanager
def lock(repository: Path) -> Iterator[None]:
    common = Path(
        git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    )
    with (common / "flowfield-integration.lock").open("ab") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ApplicationError(
                "integration_busy",
                "Another integration owns this repository. Retry when it finishes.",
                409,
            ) from error
        yield


def candidate(repository: Path, before: str, result: str, identity: str) -> str:
    if contains(repository, result, before):
        commit = before  # Revalidation of an existing combination, including manual integration.
    elif contains(repository, before, result):
        commit = result
    else:
        try:
            tree = (
                git(repository, "merge-tree", "--write-tree", before, result)
                .decode()
                .splitlines()[0]
            )
        except ApplicationError as error:
            details = git(
                repository, "merge-tree", "--write-tree", before, result, allowed_returncodes=(0, 1)
            ).decode(errors="replace")
            raise ApplicationError(
                "integration_conflict",
                "Git could not combine the target and worker result. Request a correction "
                "on this result; both commits and the target are preserved.\n" + details[:8000],
                409,
            ) from error
        commit = (
            git(
                repository,
                "-c",
                "user.name=Flowfield",
                "-c",
                "user.email=local@flowfield.invalid",
                "commit-tree",
                tree,
                "-p",
                before,
                "-p",
                result,
                "-m",
                "Validate accepted task integration",
            )
            .decode()
            .strip()
        )
    git(repository, "update-ref", f"refs/flowfield/integrations/{identity}", commit)
    return commit


def correction_seed(repository: Path, before: str, source: str, identity: str) -> tuple[str, str]:
    """Preserve both inputs, including conflict markers, without touching human work."""
    output = git(
        repository, "merge-tree", "--write-tree", before, source, allowed_returncodes=(0, 1)
    ).decode(errors="replace")
    tree = output.splitlines()[0]
    git(repository, "rev-parse", "--verify", tree + "^{tree}")
    commit = (
        git(
            repository,
            "-c",
            "user.name=Flowfield",
            "-c",
            "user.email=local@flowfield.invalid",
            "commit-tree",
            tree,
            "-p",
            before,
            "-p",
            source,
            "-m",
            "Unreviewed correction input",
        )
        .decode()
        .strip()
    )
    git(repository, "update-ref", f"refs/flowfield/corrections/{identity}", commit)
    return commit, output[:16000]


def run_checks(
    environment: LocalAttempt, commands: list[str], timeout: int = 60
) -> list[CheckResult]:
    return asyncio.run(local_checks.run_checks(environment, commands, timeout))


def unchanged(environment: LocalAttempt, commit: str) -> bool:
    snapshot, _ = environment.snapshot(commit)
    return git(environment.checkout, "rev-parse", snapshot + "^{tree}") == git(
        environment.checkout, "rev-parse", commit + "^{tree}"
    )


def apply(
    repository: Path, branch: str, before: str, after: str, checkout: CheckoutBinding
) -> None:
    git_checkout.deliver(repository, branch, before, after, checkout)
