"""Explicit integration orchestration. Git and SQLite retain separate durable evidence."""

import json
import sqlite3
from pathlib import Path
from time import monotonic
from uuid import uuid4

from flowfield.adapters import git_checkout, git_upstream
from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import contains, git
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
    IntegrationRead,
    IntegrationSettings,
    IntegrationSummary,
    ValidationProgress,
)
from flowfield.workspaces import Workspaces


class Integrations:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.execution = Execution(workspace)

    def for_task(self, workspace_id: str, task_id: str) -> IntegrationSettings:
        task = self.workspace.task(workspace_id, task_id)
        if task.project_id is None:
            raise ApplicationError("repository_required", "This task has no repository.", 409)
        return self.settings(task.project_id)

    def repository(self, project_id: str, branch: str) -> Path:
        return Path(self.workspace.project(project_id).path)

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
            with self.workspace.connection(
                write=True, workspace_id=Workspaces(self.workspace).for_project(project_id).id
            ) as db:
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
            "json_extract(data,'$.project_id')=? AND status='accepted' AND "
            "json_extract(data,'$.code_available')=1",
            (project_id,),
        )

    def get(self, workspace_id: str, identity: str) -> Integration:
        with self.workspace.connection() as db:
            return self._get(db, workspace_id, identity)

    def _get(self, db: sqlite3.Connection, workspace_id: str, identity: str) -> Integration:
        row = db.execute(
            "SELECT data FROM integrations WHERE workspace_id=? AND id=?", (workspace_id, identity)
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Integration not found in this workspace.", 404)
        return Integration.model_validate_json(row[0])

    def read(self, workspace_id: str, identity: str) -> IntegrationRead:
        record = self.get(workspace_id, identity)
        return IntegrationRead(
            **record.model_dump(),
            progress=(
                self.workspace.validation_progress.get(record.id)
                if record.status == "preparing"
                else None
            ),
        )

    def page(
        self,
        workspace_id: str,
        run_id: str | None = None,
        before: int | None = None,
        limit: int = 10,
    ) -> IntegrationPage:
        with self.workspace.connection() as db:
            self.workspace._workspace(db, workspace_id)
            limit = max(1, min(limit, 50))
            rows = db.execute(
                "SELECT number,json_remove(data,'$.checks','$.setup_checks') AS data "
                "FROM integrations "
                "WHERE workspace_id=? AND (? IS NULL OR "
                "run_id=?) AND (? IS NULL OR number<?) ORDER BY number DESC LIMIT ?",
                (workspace_id, run_id, run_id, before, before, limit + 1),
            ).fetchall()
            return IntegrationPage(
                items=[IntegrationSummary.model_validate_json(r["data"]) for r in rows[:limit]],
                next_before=rows[limit - 1]["number"] if len(rows) > limit else None,
            )

    def _save(self, record: Integration) -> Integration:
        record.revision += 1
        with self.workspace.connection(write=True, workspace_id=record.workspace_id) as db:
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
            "SELECT id FROM result_versions WHERE workspace_id=? "
            "AND json_extract(data,'$.integration_id')=?",
            (record.workspace_id, record.id),
        ).fetchone()
        if row:
            version = results._get(db, record.workspace_id, row[0])
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
                results.ready(db, version)
            elif version.status in ("ready", "delivering") and record.status == "stale":
                version.status, version.problem = "stale", record.problem
                version.problem_code = record.problem_code or "validation_stale"
                results._save(db, version)
            elif version.status == "delivering" and record.status == "integrated":
                results.complete(db, record.workspace_id, record.id)

    def head(self, workspace_id: str, task_id: str) -> str:
        settings = self.for_task(workspace_id, task_id)
        if not settings.target_branch:
            raise ApplicationError(
                "integration_target_required",
                "Choose an integration target branch and project checks in project settings "
                "before running the queue.",
                409,
            )
        return gitops.target(
            Path(self.workspace.project(settings.project_id).path), settings.target_branch
        )

    def refresh_source(self, workspace_id: str, task_id: str) -> str:
        settings = self.for_task(workspace_id, task_id)
        if not settings.target_branch:
            return self.head(workspace_id, task_id)  # Existing actionable setup error.
        repository = self.repository(settings.project_id, settings.target_branch)
        with gitops.lock(repository):
            return git_upstream.refresh(repository, settings.target_branch)

    def _accepted(self, workspace_id: str, run_id: str) -> Run:
        with self.workspace.connection() as db:
            run = self.execution._run(db, workspace_id, run_id)
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
                db, run, self.workspace._task(db, workspace_id, run.task_id)
            )
            # Reuse this read transaction: a nested connection can wait on a
            # writer whose commit is itself waiting for our existing reader.

            assert run.project_id
            if run.target_branch != self._settings(db, run.project_id).target_branch:
                raise ApplicationError(
                    "delivery_target_changed",
                    "The published delivery target changed. "
                    "Reconcile and publish the intended target before continuing.",
                    409,
                )
            return run

    def prepare(
        self,
        workspace_id: str,
        run_id: str,
        request: IntegrationPrepare,
        *,
        availability_result: str | None = None,
    ) -> Integration:
        run = self._accepted(workspace_id, run_id)
        assert run.project_id
        assert run.target_branch
        repository = self.repository(run.project_id, run.target_branch)
        with gitops.lock(repository):
            settings = self.for_task(workspace_id, run.task_id)
            assert settings.target_branch
            git_upstream.refresh(repository, settings.target_branch)
            before = self.head(workspace_id, run.task_id)
            assert settings.target_branch and run.result_commit
            self.workspace._current(run.revision, request.expected_revision)
            from flowfield.results import Results

            results = Results(self.workspace)
            version = results.page(workspace_id, run.task_id, limit=1).items[0]
            if availability_result:
                if version.id != availability_result or version.status != "delivered":
                    raise ApplicationError(
                        "result_changed", "Revalidate the current delivered version.", 409
                    )
                if not version.source_commit or not contains(
                    repository, version.source_commit, before
                ):
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
                return self.get(workspace_id, version.integration_id)
            record = Integration(
                id=uuid4().hex,
                workspace_id=workspace_id,
                project_id=run.project_id,
                task_id=run.task_id,
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
            with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
                current_version = results._get(db, workspace_id, version.id)
                results._current(db, current_version)
                if current_version.status != ("delivered" if availability_result else "preparing"):
                    raise ApplicationError(
                        "result_changed", "The result changed before preparation.", 409
                    )
                db.execute(
                    "INSERT INTO "
                    "integrations(id,workspace_id,project_id,run_id,status,data) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        record.id,
                        workspace_id,
                        run.project_id,
                        run.id,
                        record.status,
                        record.model_dump_json(),
                    ),
                )
                if availability_result:
                    current_version.availability_id = record.id
                    results._save(db, current_version)
                else:
                    current_version.integration_id = record.id
                    results._save(db, current_version)
            last_notification = 0.0

            def progress(value: ValidationProgress) -> None:
                nonlocal last_notification
                self.workspace.validation_progress[record.id] = value
                stamp = monotonic()
                if stamp - last_notification >= 0.5 and self.workspace.on_activity:
                    last_notification = stamp
                    self.workspace.on_activity(workspace_id, run.id)

            def cancelled() -> bool:
                with self.workspace.connection() as db:
                    current = results._get(db, workspace_id, version.id)
                    return current.status == "cancelled"

            progress(ValidationProgress(phase="preparing"))
            try:
                record.candidate_commit = gitops.candidate(
                    repository, before, record.result_commit, record.id
                )
                from flowfield.candidate_validation import validate

                evidence = validate(
                    self.workspace.directory / "integration-work",
                    repository,
                    record.id,
                    record.candidate_commit,
                    settings,
                    on_progress=progress,
                    cancelled=cancelled,
                )
                record.workspace = evidence.workspace
                record.setup_checks, record.checks = evidence.setup_checks, evidence.checks
                if evidence.problem:
                    raise ApplicationError(
                        evidence.problem_code or "validation_failed", evidence.problem, 409
                    )
                self._accepted(workspace_id, run_id)
                if (
                    self.head(workspace_id, run.task_id) != before
                    or self.settings(run.project_id).revision != settings.revision
                ):
                    record.status, record.problem = (
                        "stale",
                        "Target or settings changed during validation. Prepare again.",
                    )
                    record.problem_code = (
                        "settings_changed"
                        if self.settings(run.project_id).revision != settings.revision
                        else "target_changed"
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
            finally:
                self.workspace.validation_progress.pop(record.id, None)
            record.completed_at = record.validated_at = now()
            return self._save(record)

    def apply(self, workspace_id: str, identity: str, request: IntegrationApply) -> Integration:
        record = self.get(workspace_id, identity)
        repository = self.repository(record.project_id, record.target_branch)
        with gitops.lock(repository):
            record = self.get(workspace_id, identity)
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
            self._accepted(workspace_id, record.run_id)
            with self.workspace.connection() as db:
                if not db.execute(
                    "SELECT 1 FROM result_versions WHERE workspace_id=? AND status='delivering' "
                    "AND json_extract(data,'$.integration_id')=? "
                    "AND json_extract(data,'$.candidate_commit')=? "
                    "AND json_extract(data,'$.approved_at') IS NOT NULL",
                    (workspace_id, record.id, request.candidate_commit),
                ).fetchone():
                    raise ApplicationError(
                        "approval_required",
                        "Approve this exact result version before delivery.",
                        409,
                    )
            if (
                record.apply_started_at
                and gitops.target(repository, record.target_branch) == record.candidate_commit
            ):
                return self._recover_locked(record)
            git_upstream.refresh(repository, record.target_branch)
            if (
                self.settings(record.project_id).revision != record.settings_revision
                or gitops.target(repository, record.target_branch) != record.target_before
            ):
                record.status, record.problem = (
                    "stale",
                    "Target or settings moved. Prepare and validate again.",
                )
                record.problem_code = (
                    "settings_changed"
                    if self.settings(record.project_id).revision != record.settings_revision
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
                with self.workspace.connection(write=True, workspace_id=workspace_id) as db:
                    current = self.execution._run(db, workspace_id, record.run_id)
                    from flowfield.guidance_reconciliation import require_current
                    from flowfield.result_models import ResultVersion

                    result_row = db.execute(
                        "SELECT data FROM result_versions WHERE run_id=? "
                        "ORDER BY version DESC LIMIT 1",
                        (current.id,),
                    ).fetchone()
                    require_current(
                        self.workspace,
                        db,
                        self.workspace._task(db, workspace_id, current.task_id),
                        ResultVersion.model_validate_json(result_row[0]) if result_row else None,
                    )
                    self.execution._current_assignment(
                        db, current, self.workspace._task(db, workspace_id, current.task_id)
                    )
                    if (
                        current.status != "accepted"
                        or current.result_commit != record.result_commit
                        or self.settings(record.project_id).revision != record.settings_revision
                    ):
                        raise ApplicationError(
                            "integration_changed",
                            "Acceptance or settings changed. Prepare again.",
                            409,
                        )
                    approved = db.execute(
                        "SELECT id FROM result_versions WHERE workspace_id=? AND "
                        "status='delivering' "
                        "AND json_extract(data,'$.integration_id')=?",
                        (workspace_id, record.id),
                    ).fetchone()
                    if not approved:
                        raise ApplicationError(
                            "approval_required", "Exact approval is no longer current.", 409
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
            self.refresh_availability(record.project_id)
            return record

    def available(self, project_id: str, head: str, branch: str | None = None) -> set[str]:
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
                (project_id, head, branch or settings.target_branch, settings.revision),
            ).fetchone()
            commits = [
                json.loads(row[0]).get("result_commit")
                for row in db.execute(
                    "SELECT data FROM runs WHERE json_extract(data,'$.project_id')=? "
                    "AND status='accepted'",
                    (project_id,),
                )
            ]
            if not valid or valid[0] != "integrated":
                return set()
        repository = Path(self.workspace.project(project_id).path)
        return {commit for commit in commits if commit and contains(repository, commit, head)}

    def refresh_availability(self, project_id: str) -> set[str]:
        settings = self.settings(project_id)
        repository = Path(self.workspace.project(project_id).path)
        with self.workspace.connection() as db:
            observed_integrations = {
                row[0]
                for row in db.execute(
                    "SELECT data FROM integrations WHERE project_id=? AND status='ready'",
                    (project_id,),
                )
            }
            observed_runs = {
                row[0]
                for row in db.execute(
                    "SELECT data FROM runs WHERE json_extract(data,'$.project_id')=? "
                    "AND status='accepted'",
                    (project_id,),
                )
            }
            branches = {
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT json_extract(data,'$.target_branch') FROM integrations "
                    "WHERE project_id=? AND status IN ('ready','integrated')",
                    (project_id,),
                )
            }
        if not observed_integrations and not observed_runs and not branches:
            return set()
        branches.add(settings.target_branch)
        heads: dict[str | None, str | None] = {}
        available_by_branch: dict[str | None, set[str]] = {}
        for branch in branches:
            try:
                head = gitops.target(repository, branch) if branch else None
                heads[branch] = head
                available_by_branch[branch] = (
                    self.available(project_id, head, branch) if head else set()
                )
            except ApplicationError:
                heads[branch], available_by_branch[branch] = None, set()
        with self.workspace.connection(
            write=True, workspace_id=Workspaces(self.workspace).for_project(project_id).id
        ) as db:
            if self._settings(db, project_id).revision != settings.revision:
                return set()
            for row in db.execute(
                "SELECT data FROM integrations WHERE project_id=? AND status='ready'", (project_id,)
            ).fetchall():
                # Git reads happen outside the transaction. A candidate prepared or
                # changed meanwhile has not been observed by this refresh.
                if row[0] not in observed_integrations:
                    continue
                record = Integration.model_validate_json(row[0])
                if (
                    record.target_before != heads.get(record.target_branch)
                    or record.settings_revision != settings.revision
                ):
                    record.status, record.problem = (
                        "stale",
                        "Target or settings moved. Prepare and validate again.",
                    )
                    record.problem_code = (
                        "settings_changed"
                        if record.settings_revision != settings.revision
                        else "target_changed"
                    )
                    record.revision += 1
                    db.execute(
                        "UPDATE integrations SET status=?,data=? WHERE id=?",
                        (record.status, record.model_dump_json(), record.id),
                    )
                    self._sync_result(db, record)
            for row in db.execute(
                "SELECT data FROM runs WHERE json_extract(data,'$.project_id')=? AND "
                "status='accepted'",
                (project_id,),
            ).fetchall():
                if row[0] not in observed_runs:
                    continue
                run = Run.model_validate_json(row[0])
                value = run.result_commit in available_by_branch.get(run.target_branch, set())
                if run.code_available != value:
                    run.code_available = value
                    self.execution._save(db, run)
        return available_by_branch.get(settings.target_branch, set())

    def restart(self) -> None:
        with self.workspace.connection() as db:
            rows = db.execute(
                "SELECT data FROM integrations WHERE status IN ('preparing','applying')"
            ).fetchall()
        for row in rows:
            self.recover(Integration.model_validate_json(row[0]))

    def recover(self, record: Integration) -> Integration:
        repository = self.repository(record.project_id, record.target_branch)
        with gitops.lock(repository):
            return self._recover_locked(record)

    def _recover_locked(self, record: Integration) -> Integration:
        repository = self.repository(record.project_id, record.target_branch)
        record = self.get(record.workspace_id, record.id)
        if record.status not in ("preparing", "applying", "stale"):
            return record
        try:
            if self.settings(record.project_id).revision != record.settings_revision:
                raise ApplicationError("settings_changed", "Settings changed; prepare again.", 409)
            self._accepted(record.workspace_id, record.run_id)
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
