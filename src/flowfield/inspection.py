"""Prepare and inspect exact-result copies without running project commands."""

import os
import shlex
from pathlib import Path
from uuid import uuid4

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.inspection_launcher import write_launcher
from flowfield.adapters.local_execution import LocalHost
from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.inspection_models import (
    Inspection,
    InspectionConfig,
    InspectionPrepare,
    InspectionSettings,
)
from flowfield.integration import Integrations
from flowfield.results import Results


class Inspections:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.integrations = Integrations(workspace)

    def settings(self, project_id: str) -> InspectionSettings:
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            row = db.execute(
                "SELECT data FROM inspection_settings WHERE project_id=?", (project_id,)
            ).fetchone()
            return (
                InspectionSettings.model_validate_json(row[0])
                if row
                else InspectionSettings(project_id=project_id)
            )

    def configure(self, project_id: str, request: InspectionConfig) -> InspectionSettings:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            current = self.settings(project_id)
            self.workspace._current(current.revision, request.expected_revision)
            value = InspectionSettings(
                project_id=project_id,
                revision=current.revision + 1,
                run_command=request.run_command.strip(),
            )
            db.execute(
                "INSERT INTO inspection_settings VALUES (?,?) ON CONFLICT(project_id) "
                "DO UPDATE SET data=excluded.data",
                (project_id, value.model_dump_json()),
            )
            return value

    def latest(self, project_id: str, result_id: str) -> Inspection | None:
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            Results(self.workspace)._get(db, project_id, result_id)
            row = db.execute(
                "SELECT data FROM inspections WHERE project_id=? AND result_id IS ? "
                "ORDER BY number DESC LIMIT 1",
                (project_id, result_id),
            ).fetchone()
        return self._observe(Inspection.model_validate_json(row[0])) if row else None

    def get(self, project_id: str, identity: str) -> Inspection:
        with self.workspace.connection() as db:
            row = db.execute(
                "SELECT data FROM inspections WHERE project_id=? AND id=?",
                (project_id, identity),
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Inspection not found in this project.", 404)
        return self._observe(Inspection.model_validate_json(row[0]))

    def _observe(self, value: Inspection) -> Inspection:
        settings = self.integrations.settings(value.project_id)
        value.instructions_changed = (
            settings.runtime != value.runtime
            or settings.setup_commands != value.setup_commands
            or self.settings(value.project_id).run_command != value.run_command
        )
        if value.promotion_id:
            from flowfield.milestone_promotion import read

            with self.workspace.connection() as db:
                promotion = read(db, value.promotion_id)
                verification = db.execute(
                    "SELECT status,json_extract(data,'$.current') FROM milestone_verifications "
                    "WHERE id=?",
                    (promotion.verification_id,),
                ).fetchone()
            value.source_changed = (
                promotion.status in ("stale", "failed")
                or not verification
                or not verification[1]
                or verification[0] in ("stale", "failed", "changes_requested")
            )
        else:
            assert value.result_id
            result = Results(self.workspace).get(value.project_id, value.result_id)
            page = Results(self.workspace).page(value.project_id, result.task_id, limit=1)
            value.source_changed = (
                page.current_id != value.result_id or page.current_run_id != result.run_id
            )
        if value.status == "ready":
            try:
                assert value.workspace
                checkout = Path(value.workspace)
                value.locally_changed = baseline(checkout) != value.commit or bool(
                    git(checkout, "status", "--porcelain", "--untracked-files=normal")
                )
                if value.launcher and not Path(value.launcher).is_file():
                    value.problem = (
                        "The saved launcher is unavailable. Prepare another copy; "
                        "existing files are preserved."
                    )
            except (ApplicationError, OSError):
                value.problem = (
                    "Inspection files are unavailable. Prepare another copy; "
                    "existing files are preserved."
                )
        return value

    def _save(self, value: Inspection, *, insert: bool = False) -> None:
        data = value.model_dump_json(
            exclude={"locally_changed", "source_changed", "instructions_changed"}
        )
        with self.workspace.connection(write=True, project_id=value.project_id) as db:
            if insert:
                db.execute(
                    "INSERT INTO inspections(id,project_id,result_id,data,promotion_id) "
                    "VALUES (?,?,?,?,?)",
                    (value.id, value.project_id, value.result_id, data, value.promotion_id),
                )
            else:
                db.execute("UPDATE inspections SET data=? WHERE id=?", (data, value.id))

    def prepare(self, project_id: str, request: InspectionPrepare) -> Inspection:
        repository = Path(self.workspace.project(project_id).path)
        # Same repository boundary as delivery; concurrent requests cannot create duplicate copies.
        with gitops.lock(repository):
            settings = self.integrations.settings(project_id)
            instructions = self.settings(project_id)
            result = Results(self.workspace).get(project_id, request.result_id)
            self.workspace._current(result.revision, request.expected_revision)
            if (
                result.completion != "code"
                or not result.candidate_commit
                or not result.target_branch
            ):
                raise ApplicationError(
                    "candidate_missing", "This result has no prepared code candidate to try.", 409
                )
            commit, branch = result.candidate_commit, result.target_branch
            previous = self.latest(project_id, request.result_id)
            if (
                previous
                and previous.status == "ready"
                and not previous.problem
                and previous.commit == commit
                and previous.target_branch == branch
                and not previous.instructions_changed
                and not request.new_copy
            ):
                return previous  # Never reset or overwrite a dirty copy.
            if previous and previous.status == "preparing":
                previous.status = "failed"
                previous.problem = (
                    "Preparation was interrupted. Files retained; "
                    "a separate copy is being prepared."
                )
                self._save(previous)
            value = Inspection(
                id=uuid4().hex,
                project_id=project_id,
                result_id=request.result_id,
                task_key=result.task_key,
                version=result.version,
                target_branch=branch,
                commit=commit,
                created_at=now(),
                runtime=settings.runtime,
                setup_commands=settings.setup_commands,
                run_command=instructions.run_command,
            )
            return self._copy(value, repository)

    def _copy(self, value: Inspection, repository: Path) -> Inspection:
        self._save(value, insert=True)
        try:
            environment = LocalHost(os.environ).prepare(
                self.workspace.directory / "inspection-work", repository, value.id, value.commit
            )
            value.workspace = str(environment.checkout)
            if value.run_command:
                value.launcher = str(
                    write_launcher(environment, value.setup_commands, value.run_command)
                )
                value.command = f"/bin/sh {shlex.quote(value.launcher)}"
            value.status = "ready"
        except (ApplicationError, OSError) as error:
            value.status = "failed"
            value.problem = (
                error.message
                if isinstance(error, ApplicationError)
                else "Could not prepare inspection files. Check the directory and disk space."
            )
        self._save(value)
        return self._observe(value)

    def prepare_milestone(
        self, workspace_id: str, identity: str, expected_revision: int, new_copy: bool = False
    ) -> Inspection:
        from flowfield.milestone_verification import MilestoneVerifications

        verification = MilestoneVerifications(self.workspace).get(workspace_id, identity)
        self.workspace._current(verification.revision, expected_revision)
        promotion = verification.promotion
        if not promotion or not promotion.candidate_commit:
            raise ApplicationError(
                "candidate_missing", "This milestone has no prepared candidate.", 409
            )
        repository = Path(self.workspace.project(promotion.project_id).path)
        with gitops.lock(repository):
            settings = self.integrations.settings(promotion.project_id)
            instructions = self.settings(promotion.project_id)
            with self.workspace.connection() as db:
                row = db.execute(
                    "SELECT data FROM inspections WHERE promotion_id=? "
                    "ORDER BY number DESC LIMIT 1",
                    (promotion.id,),
                ).fetchone()
            previous = self._observe(Inspection.model_validate_json(row[0])) if row else None
            if (
                previous
                and previous.status == "ready"
                and not previous.problem
                and not previous.instructions_changed
                and not new_copy
            ):
                return previous
            return self._copy(
                Inspection(
                    id=uuid4().hex,
                    project_id=promotion.project_id,
                    promotion_id=promotion.id,
                    task_key=verification.milestone_key,
                    version=verification.milestone_revision,
                    target_branch=promotion.target_branch,
                    commit=promotion.candidate_commit,
                    created_at=now(),
                    runtime=settings.runtime,
                    setup_commands=settings.setup_commands,
                    run_command=instructions.run_command,
                ),
                repository,
            )
