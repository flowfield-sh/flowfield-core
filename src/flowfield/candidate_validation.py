"""Isolated candidate checks for task delivery."""

import os
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.local_checks import command_problem
from flowfield.adapters.local_execution import LocalHost
from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult, Record
from flowfield.integration_models import CommandProgress, IntegrationSettings, ValidationProgress


class Validation(Record):
    workspace: str | None = None
    setup_checks: list[CheckResult] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    problem: str | None = None
    problem_code: str | None = None


def validate(
    directory: Path,
    repository: Path,
    identity: str,
    commit: str,
    settings: IntegrationSettings,
    *,
    on_progress: Callable[[ValidationProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Validation:
    value = Validation()
    phase: str = "setup"

    def progress(command: CommandProgress, reports: list[CheckResult]) -> None:
        if on_progress:
            on_progress(
                ValidationProgress(
                    phase="setup" if phase == "setup" else "checks",
                    current=command,
                    setup_checks=reports if phase == "setup" else value.setup_checks,
                    checks=reports if phase == "checks" else [],
                )
            )

    def check_cancelled() -> None:
        if cancelled and cancelled():
            raise ApplicationError(
                "validation_cancelled", "Checks stopped; command output was retained.", 409
            )

    try:
        check_cancelled()
        environment = LocalHost(os.environ).prepare(directory, repository, identity, commit)
        value.workspace = str(environment.checkout)
        value.setup_checks = gitops.run_checks(
            environment,
            settings.setup_commands,
            settings.setup_timeout_seconds,
            on_progress=progress if on_progress else None,
            cancelled=cancelled,
        )
        check_cancelled()
        if any(check.exit_code for check in value.setup_checks):
            raise ApplicationError(
                "runtime_setup_failed", command_problem(value.setup_checks, []), 409
            )
        phase = "checks"
        value.checks = gitops.run_checks(
            environment,
            settings.checks,
            settings.check_timeout_seconds,
            on_progress=progress if on_progress else None,
            cancelled=cancelled,
        )
        check_cancelled()
        if on_progress:
            on_progress(
                ValidationProgress(
                    phase="verifying", setup_checks=value.setup_checks, checks=value.checks
                )
            )
        if not gitops.unchanged(environment, commit):
            raise ApplicationError(
                "candidate_changed",
                "Validation changed candidate code. Preserve those changes as a "
                "new reviewed task result.",
                409,
            )
        if any(check.exit_code for check in value.checks):
            raise ApplicationError(
                "checks_failed", "Project checks failed. Inspect their output before retrying.", 409
            )
    except (ApplicationError, OSError) as error:
        value.problem = str(error)
        value.problem_code = (
            error.code if isinstance(error, ApplicationError) else "validation_failed"
        )
    return value
