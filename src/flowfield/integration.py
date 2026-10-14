"""Explicit integration orchestration. Git and SQLite retain separate durable evidence."""

import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from flowfield.adapters import git_checkout
from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import contains, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import Run
from flowfield.integration_models import (
    DELIVERY_BLOCKERS,
    Integration,
    IntegrationApply,
    IntegrationConfig,
    IntegrationPage,
    IntegrationPrepare,
    IntegrationSettings,
    IntegrationSummary,
)


class Integrations:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.execution = Execution(workspace)

    def settings(self, project_id: str) -> IntegrationSettings:
        with self.workspace.connection() as db:
            value = self._settings(db, project_id)
            repository = Path(self.workspace._project(db, project_id).path)
        if value.target_branch is None:
            try:
                value.target_branch = git_checkout.current_branch(repository)
            except ApplicationError:
                pass
        return value

    def _settings(self, db: sqlite3.Connection, project_id: str) -> IntegrationSettings:
        self.workspace._project(db, project_id)
        row = db.execute(
            "SELECT data FROM integration_settings WHERE project_id=?", (project_id,)
        ).fetchone()
        return (
            IntegrationSettings.model_validate_json(row[0])
            if row
            else IntegrationSettings(project_id=project_id)
        )

    def configure(self, project_id: str, request: IntegrationConfig) -> IntegrationSettings:
        repository = Path(self.workspace.project(project_id).path)
        if any(
            not command.strip() or len(command) > 4000
            for command in [*request.checks, *request.setup_commands]
        ):
            raise ApplicationError(
                "invalid_checks",
                "Provide 1–10 nonempty project check commands, each at most 4000 characters.",
            )
        with gitops.lock(repository):
            ref = gitops.branch_ref(repository, request.target_branch)
            with self.workspace.connection(write=True, project_id=project_id) as db:
                current = self._settings(db, project_id)
                self.workspace._current(current.revision, request.expected_revision)
                runtime = request.runtime or current.runtime
                if request.create_from:
                    base = gitops.resolve(repository, request.create_from)
                    # Creation is explicit and never resets an existing branch.
                    git(repository, "update-ref", ref, base, "0" * len(base))
                else:
                    try:
                        gitops.target(repository, request.target_branch)
                    except ApplicationError as error:
                        if error.code != "missing_destination":
                            raise
                        base = gitops.resolve(repository, "HEAD")
                        # Saving a destination creates a missing branch, never resets one.
                        git(repository, "update-ref", ref, base, "0" * len(base))
                value = IntegrationSettings(
                    project_id=project_id,
                    revision=current.revision + 1,
                    runtime=runtime,
                    target_branch=request.target_branch,
                    checks=request.checks,
                    setup_commands=request.setup_commands
                    if "setup_commands" in request.model_fields_set
                    else current.setup_commands,
                    setup_timeout_seconds=request.setup_timeout_seconds
                    if "setup_timeout_seconds" in request.model_fields_set
                    else current.setup_timeout_seconds,
                    check_timeout_seconds=request.check_timeout_seconds
                    if "check_timeout_seconds" in request.model_fields_set
                    else current.check_timeout_seconds,
                )
                db.execute(
                    "INSERT INTO integration_settings VALUES (?,?) ON CONFLICT(project_id) "
                    "DO UPDATE SET data=excluded.data",
                    (project_id, value.model_dump_json()),
                )
                self._invalidate_availability(db, project_id)
                return value

    @staticmethod
    def _invalidate_availability(db: sqlite3.Connection, project_id: str) -> None:
        db.execute(
            "UPDATE runs SET data=json_set(data,'$.code_available',json('false'),"
            "'$.revision',json_extract(data,'$.revision')+1) WHERE "
            "project_id=? AND status='accepted' AND json_extract(data,'$.code_available')=1",
            (project_id,),
        )

    def get(self, project_id: str, identity: str) -> Integration:
        with self.workspace.connection() as db:
            row = db.execute(
                "SELECT data FROM integrations WHERE project_id=? AND id=?", (project_id, identity)
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Integration not found in this project.", 404)
            return Integration.model_validate_json(row[0])

    def page(
        self, project_id: str, run_id: str | None = None, before: int | None = None, limit: int = 10
    ) -> IntegrationPage:
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            limit = max(1, min(limit, 50))
            rows = db.execute(
                "SELECT number,json_remove(data,'$.checks','$.setup_checks') AS data "
                "FROM integrations "
                "WHERE project_id=? AND (? IS NULL OR "
                "run_id=?) AND (? IS NULL OR number<?) ORDER BY number DESC LIMIT ?",
                (project_id, run_id, run_id, before, before, limit + 1),
            ).fetchall()
            return IntegrationPage(
                items=[IntegrationSummary.model_validate_json(r["data"]) for r in rows[:limit]],
                next_before=rows[limit - 1]["number"] if len(rows) > limit else None,
            )

    def _save(self, record: Integration) -> Integration:
        record.revision += 1
        with self.workspace.connection(write=True, project_id=record.project_id) as db:
            db.execute(
                "UPDATE integrations SET status=?, data=? WHERE id=?",
                (record.status, record.model_dump_json(), record.id),
            )
            self._sync_result(db, record)
        return record

    def _sync_result(self, db: sqlite3.Connection, record: Integration) -> None:
        from flowfield.results import Results

        results = Results(self.workspace)
        row = db.execute(
            "SELECT id FROM result_versions WHERE project_id=? "
            "AND json_extract(data,'$.integration_id')=?",
            (record.project_id, record.id),
        ).fetchone()
        if row:
            version = results._get(db, record.project_id, row[0])
            if version.status == "preparing" and record.status in ("ready", "failed", "stale"):
                version.candidate_commit = record.candidate_commit
                version.target_before = record.target_before
                version.settings_revision = record.settings_revision
                version.status = "ready" if record.status == "ready" else "blocked"
                version.problem = record.problem
                version.problem_code = record.problem_code
                if version.status == "ready" and version.report.outcome == "partial":
                    version.status = "blocked"
                    version.problem_code = "partial_outcome"
                    version.problem = version.report.remaining_work
                results._save(db, version)
            elif version.status in ("ready", "delivering") and record.status == "stale":
                version.status, version.problem = "stale", record.problem
                version.problem_code = record.problem_code or "validation_stale"
                results._save(db, version)
            elif version.status == "delivering" and record.status == "integrated":
                results.complete(db, record.project_id, record.id)

    def head(self, project_id: str) -> str:
        settings = self.settings(project_id)
        if not settings.target_branch:
            raise ApplicationError(
                "integration_target_required",
                "Choose an integration target branch and project checks in project settings "
                "before running the queue.",
                409,
            )
        return gitops.target(Path(self.workspace.project(project_id).path), settings.target_branch)

    def _accepted(self, project_id: str, run_id: str) -> Run:
        with self.workspace.connection() as db:
            run = self.execution._run(db, project_id, run_id)
            if (
                run.status not in ("in_review", "accepted")
                or not run.result_commit
                or run.completion != "code"
            ):
                raise ApplicationError(
                    "not_accepted",
                    "Capture a code result before preparing integration.",
                    409,
                )
            self.execution._current_assignment(
                run, self.workspace._task(db, project_id, run.task_id)
            )
            # Reuse this read transaction: a nested connection can wait on a
            # writer whose commit is itself waiting for our existing reader.
            if run.target_branch != self._settings(db, project_id).target_branch:
                raise ApplicationError(
                    "delivery_target_changed",
                    "The published delivery target changed. "
                    "Reconcile and publish the intended target before continuing.",
                    409,
                )
            return run

    def prepare(
        self,
        project_id: str,
        run_id: str,
        request: IntegrationPrepare,
        *,
        availability_result: str | None = None,
    ) -> Integration:
        repository = Path(self.workspace.project(project_id).path)
        with gitops.lock(repository):
            settings = self.settings(project_id)
            before = self.head(project_id)
            run = self._accepted(project_id, run_id)
            assert settings.target_branch and run.result_commit
            self.workspace._current(run.revision, request.expected_revision)
            from flowfield.results import Results

            results = Results(self.workspace)
            version = results.page(project_id, run.task_id, limit=1).items[0]
            if availability_result:
                if version.id != availability_result or version.status != "delivered":
                    raise ApplicationError(
                        "result_changed", "Revalidate the current delivered version.", 409
                    )
                if not contains(repository, version.source_commit, before):
                    raise ApplicationError(
                        "delivered_code_missing",
                        "The target lost delivered code. Review restoration with the coordinator.",
                        409,
                    )
            elif version.run_id != run.id or version.status != "preparing":
                raise ApplicationError(
                    "result_changed",
                    "Preparation requires the current pending result version.",
                    409,
                )
            if version.integration_id and not availability_result:
                return self.get(project_id, version.integration_id)
            record = Integration(
                id=uuid4().hex,
                project_id=project_id,
                run_id=run.id,
                task_key=run.task_key,
                settings_revision=settings.revision,
                target_branch=settings.target_branch,
                target_before=before,
                checkout=git_checkout.binding(repository),
                result_commit=run.result_commit,
                created_at=now(),
                author=request.author,
                result_id=version.id,
                purpose="availability" if availability_result else "preparation",
            )
            with self.workspace.connection(write=True, project_id=project_id) as db:
                current_version = results._get(db, project_id, version.id)
                results._current(db, current_version)
                if current_version.status != ("delivered" if availability_result else "preparing"):
                    raise ApplicationError(
                        "result_changed", "The result changed before preparation.", 409
                    )
                db.execute(
                    "INSERT INTO integrations(id,project_id,run_id,status,data) VALUES (?,?,?,?,?)",
                    (record.id, project_id, run.id, record.status, record.model_dump_json()),
                )
                if availability_result:
                    current_version.availability_id = record.id
                    results._save(db, current_version)
                else:
                    current_version.integration_id = record.id
                    results._save(db, current_version)
            try:
                record.candidate_commit = gitops.candidate(
                    repository, before, record.result_commit, record.id
                )
                environment = LocalHost(os.environ).prepare(
                    self.workspace.directory / "integration-work",
                    repository,
                    record.id,
                    record.candidate_commit,
                )
                record.workspace = str(environment.checkout)
                self._save(record)
                record.setup_checks = gitops.run_checks(
                    environment, settings.setup_commands, settings.setup_timeout_seconds
                )
                if any(check.exit_code for check in record.setup_checks):
                    from flowfield.adapters.local_checks import command_problem

                    raise ApplicationError(
                        "runtime_setup_failed",
                        command_problem(record.setup_checks, []),
                        409,
                    )
                record.checks = gitops.run_checks(
                    environment, settings.checks, settings.check_timeout_seconds
                )
                if not gitops.unchanged(environment, record.candidate_commit):
                    raise ApplicationError(
                        "candidate_changed",
                        "Validation changed candidate code. Preserved changes need a new "
                        "reviewed task result; they are not approved by the original acceptance.",
                        409,
                    )
                if any(check.exit_code for check in record.checks):
                    raise ApplicationError(
                        "checks_failed",
                        "Validation failed. Inspect the output and request a correction "
                        "on this result; the target is unchanged.",
                        409,
                    )
                self._accepted(project_id, run_id)
                if (
                    self.head(project_id) != before
                    or self.settings(project_id).revision != settings.revision
                ):
                    record.status, record.problem = (
                        "stale",
                        "Target or settings changed during validation. Prepare again.",
                    )
                else:
                    record.status = "integrated" if availability_result else "ready"
                    if availability_result:
                        record.target_after = before
            except (ApplicationError, OSError) as error:
                record.status, record.problem = "failed", str(error)
                record.problem_code = (
                    error.code if isinstance(error, ApplicationError) else "local_io_failed"
                )
            record.completed_at = record.validated_at = now()
            return self._save(record)

    def apply(self, project_id: str, identity: str, request: IntegrationApply) -> Integration:
        repository = Path(self.workspace.project(project_id).path)
        with gitops.lock(repository):
            record = self.get(project_id, identity)
            self.workspace._current(record.revision, request.expected_revision)
            eligible = record.status == "ready" or (
                record.status == "stale" and record.problem_code in DELIVERY_BLOCKERS
            )
            if not eligible or record.candidate_commit != request.candidate_commit:
                raise ApplicationError(
                    "integration_changed",
                    "Inspect the current ready candidate before applying it.",
                    409,
                )
            self._accepted(project_id, record.run_id)
            with self.workspace.connection() as db:
                if not db.execute(
                    "SELECT 1 FROM result_versions WHERE project_id=? AND status='delivering' "
                    "AND json_extract(data,'$.integration_id')=? "
                    "AND json_extract(data,'$.candidate_commit')=? "
                    "AND json_extract(data,'$.approved_at') IS NOT NULL",
                    (project_id, record.id, request.candidate_commit),
                ).fetchone():
                    raise ApplicationError(
                        "approval_required",
                        "Approve this exact result version before delivery.",
                        409,
                    )
            if record.apply_started_at and self.head(project_id) == record.candidate_commit:
                return self._recover_locked(record)
            if (
                self.settings(project_id).revision != record.settings_revision
                or self.head(project_id) != record.target_before
            ):
                record.status, record.problem = (
                    "stale",
                    "Target or settings moved. Prepare and validate again.",
                )
                record.problem_code = (
                    "settings_changed"
                    if self.settings(project_id).revision != record.settings_revision
                    else "target_changed"
                )
                return self._save(record)
            git_checkout.verify(
                repository, record.target_branch, record.target_before, record.checkout
            )
            record.status, record.author = "applying", request.author
            record.apply_started_at = now()
            self._save(record)  # Durable intent before the non-transactional Git update.
            try:
                # Keep the final intent check and short ref update together. Validation
                # itself runs outside database transactions; the durable applying record
                # allows recovery if the process exits between Git and SQLite commits.
                with self.workspace.connection(write=True, project_id=project_id) as db:
                    current = self.execution._run(db, project_id, record.run_id)
                    self.execution._current_assignment(
                        current, self.workspace._task(db, project_id, current.task_id)
                    )
                    if (
                        current.status != "accepted"
                        or current.result_commit != record.result_commit
                        or self.settings(project_id).revision != record.settings_revision
                    ):
                        raise ApplicationError(
                            "integration_changed",
                            "Acceptance or settings changed. Prepare again.",
                            409,
                        )
                    gitops.apply(
                        repository,
                        record.target_branch,
                        record.target_before,
                        request.candidate_commit,
                        record.checkout,
                    )
            except ApplicationError as error:
                record.status, record.problem, record.problem_code = "stale", str(error), error.code
                return self._save(record)
            record.status, record.target_after, record.completed_at = (
                "integrated",
                request.candidate_commit,
                now(),
            )
            record.checkout_verified_at = now()
            record.problem = record.problem_code = None
            self._save(record)
            self.refresh_availability(project_id)
            return record

    def available(self, project_id: str, head: str) -> set[str]:
        settings = self.settings(project_id)
        with self.workspace.connection() as db:
            valid = db.execute(
                "SELECT status FROM integrations WHERE project_id=? "
                "AND (status='integrated' OR (json_extract(data,'$.purpose')='availability' "
                "AND status='failed')) "
                "AND coalesce(json_extract(data,'$.target_after'), "
                "json_extract(data,'$.target_before'))=? "
                "AND json_extract(data,'$.target_branch')=? "
                "AND json_extract(data,'$.settings_revision')=? ORDER BY number DESC LIMIT 1",
                (project_id, head, settings.target_branch, settings.revision),
            ).fetchone()
            commits = [
                json.loads(row[0]).get("result_commit")
                for row in db.execute(
                    "SELECT data FROM work_runs WHERE project_id=? AND status='accepted'",
                    (project_id,),
                )
            ]
        if not valid or valid[0] != "integrated":
            return set()
        repository = Path(self.workspace.project(project_id).path)
        return {commit for commit in commits if commit and contains(repository, commit, head)}

    def refresh_availability(self, project_id: str) -> set[str]:
        try:
            head = self.head(project_id)
            available = self.available(project_id, head)
        except ApplicationError:
            head = None
            available = set()
        settings = self.settings(project_id)
        with self.workspace.connection(write=True, project_id=project_id) as db:
            for row in db.execute(
                "SELECT data FROM integrations WHERE project_id=? AND status='ready'", (project_id,)
            ).fetchall():
                record = Integration.model_validate_json(row[0])
                if record.target_before != head or record.settings_revision != settings.revision:
                    record.status, record.problem = (
                        "stale",
                        "Target or settings moved. Prepare and validate again.",
                    )
                    record.revision += 1
                    db.execute(
                        "UPDATE integrations SET status=?,data=? WHERE id=?",
                        (record.status, record.model_dump_json(), record.id),
                    )
                    self._sync_result(db, record)
            for row in db.execute(
                "SELECT data FROM work_runs WHERE project_id=? AND status='accepted'", (project_id,)
            ).fetchall():
                run = self.execution._run(db, project_id, json.loads(row[0])["id"])
                value = run.result_commit in available
                if run.code_available != value:
                    run.code_available = value
                    self.execution._save(db, run)
        return available

    def restart(self) -> None:
        with self.workspace.connection() as db:
            rows = db.execute(
                "SELECT data FROM integrations WHERE status IN ('preparing','applying')"
            ).fetchall()
        for row in rows:
            self.recover(Integration.model_validate_json(row[0]))

    def recover(self, record: Integration) -> Integration:
        repository = Path(self.workspace.project(record.project_id).path)
        with gitops.lock(repository):
            return self._recover_locked(record)

    def _recover_locked(self, record: Integration) -> Integration:
        repository = Path(self.workspace.project(record.project_id).path)
        record = self.get(record.project_id, record.id)
        if record.status not in ("preparing", "applying", "stale"):
            return record
        try:
            if self.settings(record.project_id).revision != record.settings_revision:
                raise ApplicationError("settings_changed", "Settings changed; prepare again.", 409)
            self._accepted(record.project_id, record.run_id)
            if (
                record.apply_started_at
                and gitops.target(repository, record.target_branch) == record.candidate_commit
            ):
                assert record.candidate_commit
                git_checkout.verify(
                    repository, record.target_branch, record.candidate_commit, record.checkout
                )
                record.status, record.target_after = "integrated", record.candidate_commit
                record.checkout_verified_at = now()
                record.problem = record.problem_code = None
            elif record.apply_started_at:
                git_checkout.verify(
                    repository, record.target_branch, record.target_before, record.checkout
                )
                record.status = "ready"
                record.problem = record.problem_code = None
            else:
                record.status, record.problem_code = "stale", "validation_stale"
                record.problem = "Service stopped during validation. Prepare again."
        except (ApplicationError, OSError) as error:
            record.status, record.problem_code = "stale", "delivery_uncertain"
            if isinstance(error, ApplicationError) and error.code in (
                "settings_changed",
                "assignment_changed",
                "delivery_destination_changed",
                "target_changed",
            ):
                record.problem_code = error.code
            record.problem = (
                f"Check the project checkout before retrying interrupted delivery: {error}"
            )
        record.completed_at = now()
        return self._save(record)
