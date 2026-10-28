"""Reuse an exact successful receipt, never infer success from matching file trees."""

from pathlib import Path

from flowfield.adapters.git_workspace import git
from flowfield.application import Workspace
from flowfield.integration_models import Integration, IntegrationSettings


def matching_receipt(
    store: Workspace, repository: Path, current: Integration, settings: IntegrationSettings
) -> Integration | None:
    if current.purpose != "preparation":
        return None  # Explicit delivered-code revalidation always executes checks.
    with store.connection() as db:
        rows = db.execute(
            "SELECT data FROM integrations WHERE workspace_id=? AND project_id=? "
            "AND status='ready' AND json_extract(data,'$.task_id')=? "
            "AND json_extract(data,'$.result_commit')=? "
            "AND json_extract(data,'$.target_before')=? "
            "AND json_extract(data,'$.settings_revision')=? "
            "AND json_extract(data,'$.validation_epoch')=? "
            "ORDER BY number DESC LIMIT 20",
            (
                current.workspace_id,
                current.project_id,
                current.task_id,
                current.result_commit,
                current.target_before,
                settings.revision,
                store.validation_epoch,
            ),
        ).fetchall()
    for row in rows:
        previous = Integration.model_validate_json(row[0])
        if (
            previous.run_id == current.run_id  # An explicit reprepare asks for new validation.
            or previous.target_branch != current.target_branch
            or previous.checkout != current.checkout
            or not previous.candidate_commit
            or not previous.validated_at
            or previous.problem
            or [check.command for check in previous.setup_checks] != settings.setup_commands
            or [check.command for check in previous.checks] != settings.checks
            or any(check.exit_code for check in [*previous.setup_checks, *previous.checks])
        ):
            continue
        # The exact checked object must still exist; the new receipt retains it.
        saved = (
            git(
                repository,
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/flowfield/integrations/{previous.id}^{{commit}}",
                allowed_returncodes=(0, 1),
            )
            .decode()
            .strip()
        )
        if saved == previous.candidate_commit:
            return previous
    return None
