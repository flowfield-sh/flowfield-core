"""Scoped retained Git comparisons and command receipts shared by GUI and MCP."""

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult

if TYPE_CHECKING:
    from flowfield.supervisor import Supervisor

EvidenceSource = Literal["run", "integration"]
CheckPhase = Literal["setup", "checks"]


def comparison(
    service: "Supervisor", workspace: str, source: EvidenceSource, identity: str
) -> tuple[Path, str, str]:
    if source == "run":
        run = service.execution.get(workspace, identity)
        environment = service.environment(identity)
        if not environment or not run.result_commit:
            raise ApplicationError(
                "result_missing", "This attempt has no captured code result yet.", 409
            )
        return environment.checkout, run.base_commit, run.result_commit
    integration = service.integrations.get(workspace, identity)
    if not integration.candidate_commit:
        raise ApplicationError(
            "candidate_missing", "This integration has no combined candidate.", 409
        )
    return (
        Path(service.workspace.project(integration.project_id).path),
        integration.target_before,
        integration.candidate_commit,
    )


def command_receipts(
    service: "Supervisor", workspace: str, source: EvidenceSource, identity: str, phase: CheckPhase
) -> tuple[Path, list[CheckResult]]:
    if source == "run":
        run = service.execution.get(workspace, identity)
        environment = service.environment(identity)
        if not environment:
            raise ApplicationError("not_found", "No command environment retained.", 404)
        return (
            environment.runtime,
            run.setup_checks if phase == "setup" else run.project_check_results,
        )
    value = service.integrations.read(workspace, identity)
    if not value.workspace:
        raise ApplicationError("not_found", "No command environment retained.", 404)
    checks = value.setup_checks if phase == "setup" else value.checks
    if value.progress:
        checks = value.progress.setup_checks if phase == "setup" else value.progress.checks
    return Path(value.workspace).parent / "runtime", checks


def setup_receipts(
    service: "Supervisor", project: str, identity: str, phase: CheckPhase
) -> tuple[Path, list[CheckResult]]:
    value = service.setup_validation.get(project, identity)
    if not value or not value.workspace:
        raise ApplicationError("not_found", "No command environment retained.", 404)
    return Path(
        value.workspace
    ).parent / "runtime", value.setup if phase == "setup" else value.checks
