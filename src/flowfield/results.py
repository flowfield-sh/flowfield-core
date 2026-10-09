"""Version-bound review and durable service delivery. No model planning loop."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import git
from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import Correction, Run, RunAction
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationApply, IntegrationPrepare
from flowfield.result_models import ResultPage, ResultReview, ResultVersion


class Results:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.execution = Execution(workspace)

    def _get(self, db: sqlite3.Connection, project_id: str, identity: str) -> ResultVersion:
        row = db.execute(
            "SELECT data FROM result_versions WHERE project_id=? AND id=?", (project_id, identity)
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Result version not found in this project.", 404)
        return ResultVersion.model_validate_json(row[0])

    def get(self, project_id: str, identity: str) -> ResultVersion:
        with self.workspace.connection() as db:
            return self._present(db, self._get(db, project_id, identity))

    def _present(self, db: sqlite3.Connection, value: ResultVersion) -> ResultVersion:
        from flowfield.result_actions import result_action

        value.next_action = result_action(self.workspace, db, value)
        value.recheck_of = db.execute(
            "SELECT min(version) FROM result_versions WHERE run_id=? AND version<?",
            (value.run_id, value.version),
        ).fetchone()[0]
        return value

    def page(
        self, project_id: str, task_id: str, before: int | None = None, limit: int = 10
    ) -> ResultPage:
        with self.workspace.connection() as db:
            task = self.workspace._task(db, project_id, task_id)
            limit = max(1, min(limit, 50))
            rows = db.execute(
                "SELECT version,data FROM result_versions WHERE project_id=? AND task_id=? "
                "AND (? IS NULL OR version<?) ORDER BY version DESC LIMIT ?",
                (project_id, task.id, before, before, limit + 1),
            ).fetchall()
            return ResultPage(
                items=[
                    self._present(db, ResultVersion.model_validate_json(row["data"]))
                    for row in rows[:limit]
                ],
                current_id=(
                    db.execute(
                        "SELECT id FROM result_versions WHERE project_id=? AND task_id=? "
                        "ORDER BY version DESC LIMIT 1",
                        (project_id, task.id),
                    ).fetchone()
                    or [None]
                )[0],
                current_run_id=(
                    db.execute(
                        "SELECT id FROM work_runs WHERE project_id=? AND task_id=? "
                        "ORDER BY number DESC LIMIT 1",
                        (project_id, task.id),
                    ).fetchone()
                    or [None]
                )[0],
                next_before=rows[limit - 1]["version"] if len(rows) > limit else None,
            )

    def _save(self, db: sqlite3.Connection, version: ResultVersion) -> None:
        version.revision += 1
        db.execute(
            "UPDATE result_versions SET status=?,data=? WHERE id=?",
            (
                version.status,
                version.model_dump_json(exclude={"next_action", "recheck_of"}),
                version.id,
            ),
        )

    def capture(self, db: sqlite3.Connection, run: Run) -> ResultVersion:
        assert run.result and run.result_commit
        number = db.execute(
            "SELECT coalesce(max(version),0)+1 FROM result_versions "
            "WHERE project_id=? AND task_id=?",
            (run.project_id, run.task_id),
        ).fetchone()[0]
        version = ResultVersion(
            id=uuid4().hex,
            project_id=run.project_id,
            task_id=run.task_id,
            task_key=run.task_key,
            version=number,
            run_id=run.id,
            completion=run.completion,
            source_commit=run.result_commit,
            report=run.result,
            created_at=now(),
            target_branch=run.target_branch,
        )
        db.execute(
            "INSERT INTO result_versions(id,project_id,task_id,run_id,version,status,data) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                version.id,
                version.project_id,
                version.task_id,
                run.id,
                number,
                version.status,
                version.model_dump_json(exclude={"next_action", "recheck_of"}),
            ),
        )
        return self._present(db, version)

    def _current(self, db: sqlite3.Connection, version: ResultVersion) -> Run:
        latest = db.execute(
            "SELECT id FROM result_versions WHERE project_id=? AND task_id=? "
            "ORDER BY version DESC LIMIT 1",
            (version.project_id, version.task_id),
        ).fetchone()
        last_run = db.execute(
            "SELECT id FROM work_runs WHERE project_id=? AND task_id=? ORDER BY "
            "number DESC LIMIT 1",
            (version.project_id, version.task_id),
        ).fetchone()
        if not latest or latest[0] != version.id or not last_run or last_run[0] != version.run_id:
            raise ApplicationError(
                "result_superseded", "A newer result or attempt is current.", 409
            )
        if (
            db.execute(
                "SELECT 1 FROM runs WHERE project_id=? AND task_id=? AND status IN "
                "('preparing','running','stopping','uncertain')",
                (version.project_id, version.task_id),
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM task_replies WHERE project_id=? AND task_id=? AND "
                "json_extract(data,'$.status')='pending'",
                (version.project_id, version.task_id),
            ).fetchone()
        ):
            raise ApplicationError(
                "input_closed", "Wait for the current reply before reviewing this result.", 409
            )
        return self.execution._run(db, version.project_id, version.run_id)

    def review(self, project_id: str, identity: str, request: ResultReview) -> ResultVersion:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            return self._review(db, project_id, identity, request)

    def _review(
        self, db: sqlite3.Connection, project_id: str, identity: str, request: ResultReview
    ) -> ResultVersion:
        version = self._get(db, project_id, identity)
        binding = version.candidate_commit or version.source_commit
        if request.candidate_commit != binding:
            raise ApplicationError(
                "result_changed", "Inspect the exact current candidate first.", 409
            )
        # A replay returns its durable operation, including a later blocked outcome.
        if request.action == "approve" and version.approved_at:
            return self._present(db, version)
        self.workspace._current(version.revision, request.expected_revision)
        run = self._current(db, version)
        task = self.workspace._task(db, project_id, version.task_id)
        if request.action == "request_changes":
            if (
                version.completion == "code"
                and version.status in ("blocked", "stale")
                and version.problem_code != "partial_outcome"
            ):
                raise ApplicationError(
                    "correction_required",
                    "Request a bounded correction to retain both code inputs and failures.",
                    409,
                )
            if version.status in ("delivering", "delivered", "changes_requested"):
                raise ApplicationError(
                    "result_changed", "This version is already closed for changes.", 409
                )
            version.status, version.feedback = "changes_requested", request.note
            run.status, run.feedback = "changes_requested", request.note
            self.execution._task_state(
                db, task, "up_next", f"Changes requested: {request.note}", request.author
            )
        else:
            if version.report.outcome != "complete":
                raise ApplicationError(
                    "partial_outcome", "Finish the agreed outcome before approval.", 409
                )
            if version.completion == "report":
                raise ApplicationError(
                    "report_approval_unnecessary",
                    "Report-only findings finish on delivery; no approval is required.",
                    409,
                )
            if version.status != "ready":
                raise ApplicationError(
                    "result_not_ready",
                    "Wait for successful candidate validation before approval.",
                    409,
                )
            self.execution._current_assignment(run, task)
            if version.completion == "code":
                assert version.integration_id
                integration = Integrations(self.workspace).get(project_id, version.integration_id)
                if integration.status != "ready" or integration.candidate_commit != binding:
                    raise ApplicationError(
                        "result_changed",
                        "The prepared candidate changed; prepare a new version.",
                        409,
                    )
            version.approval_note = request.note.strip()
            version.approved_at, version.approved_by = now(), request.author
            run.status, run.accepted_at, run.accepted_by = (
                "accepted",
                version.approved_at,
                request.author,
            )
            version.status = "delivering"
        self.execution._save(db, run)
        self._save(db, version)
        return self._present(db, version)

    def complete(self, db: sqlite3.Connection, project_id: str, integration_id: str) -> None:
        from flowfield.integration_models import Integration

        evidence = Integration.model_validate_json(
            db.execute(
                "SELECT data FROM integrations WHERE project_id=? AND id=?",
                (project_id, integration_id),
            ).fetchone()[0]
        )
        if evidence.status != "integrated" or not evidence.checkout_verified_at:
            raise ApplicationError(
                "delivery_unverified", "Project checkout delivery has not been verified.", 409
            )
        row = db.execute(
            "SELECT id FROM result_versions WHERE project_id=? AND status='delivering' "
            "AND json_extract(data,'$.integration_id')=?",
            (project_id, integration_id),
        ).fetchone()
        if not row:
            return
        version = self._get(db, project_id, row[0])
        try:
            run = self._current(db, version)
            task = self.workspace._task(db, project_id, version.task_id)
            self.execution._current_assignment(run, task)
        except ApplicationError as error:
            version.status = "stale"
            version.problem = (
                f"Code reached the target, but task intent changed. Reconcile the task: {error}"
            )
            self._save(db, version)
            return
        version.status, version.completed_at = "delivered", now()
        self._save(db, version)
        self.execution._task_state(
            db,
            task,
            "done",
            f"Delivered result version {version.version} to {version.target_branch}.",
            version.approved_by or "flowfield",
        )

    def reprepare(self, project_id: str, identity: str, request: RunAction) -> ResultVersion:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            version = self._get(db, project_id, identity)
            self.workspace._current(version.revision, request.expected_revision)
            run = self._current(db, version)
            task = self.workspace._task(db, project_id, run.task_id)
            self.execution._current_assignment(run, task)
            if version.status not in ("ready", "blocked", "stale", "delivered", "cancelled"):
                raise ApplicationError(
                    "result_busy", "Wait for the current operation before preparing again.", 409
                )
            if version.status == "delivered":
                raise ApplicationError(
                    "result_delivered",
                    "Already delivered. Revalidate current code availability separately.",
                    409,
                )
            run.status = "in_review"
            self.execution._save(db, run)
            return self.capture(db, run)

    def retry_delivery(self, project_id: str, identity: str, request: RunAction) -> ResultVersion:
        from flowfield.integration_models import DELIVERY_BLOCKERS

        with self.workspace.connection(write=True, project_id=project_id) as db:
            version = self._get(db, project_id, identity)
            self.workspace._current(version.revision, request.expected_revision)
            run = self._current(db, version)
            self.execution._current_assignment(
                run, self.workspace._task(db, project_id, version.task_id)
            )
            if (
                not version.approved_at
                or version.status not in ("blocked", "stale")
                or version.problem_code not in DELIVERY_BLOCKERS
            ):
                raise ApplicationError(
                    "delivery_not_retryable",
                    "Only unchanged approved delivery with a checkout blocker can be retried.",
                    409,
                )
            service = Integrations(self.workspace)
            if service.settings(project_id).revision != version.settings_revision:
                version.status, version.problem_code = "stale", "settings_changed"
                version.problem = "Settings changed. Prepare and approve a new candidate."
                self._save(db, version)
                return self._present(db, version)
            assert version.integration_id
            record = service.get(project_id, version.integration_id)
            allowed = {version.target_before}
            if record.apply_started_at:
                allowed.add(version.candidate_commit)
            if service.head(project_id) not in allowed:
                version.status, version.problem_code = "stale", "target_changed"
                version.problem = "Destination moved. Prepare and approve a new candidate."
                self._save(db, version)
                return self._present(db, version)
            version.status = "delivering"
            version.problem = version.problem_code = None
            self._save(db, version)
            return self._present(db, version)

    def cancel(self, project_id: str, identity: str, request: RunAction) -> ResultVersion:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            version = self._get(db, project_id, identity)
            self.workspace._current(version.revision, request.expected_revision)
            run = self._current(db, version)
            if version.status not in ("preparing", "ready", "delivering", "blocked", "stale"):
                raise ApplicationError(
                    "result_closed", "This result operation is already closed.", 409
                )
            if (
                version.integration_id
                and Integrations(self.workspace).get(project_id, version.integration_id).status
                == "applying"
            ):
                raise ApplicationError(
                    "delivery_started",
                    "Branch update started. Inspect its reconciled outcome before further action.",
                    409,
                )
            version.status, version.problem = (
                "cancelled",
                "Cancelled. Code and evidence remain; prepare a new version to continue.",
            )
            run.status = "in_review"
            self.execution._save(db, run)
            self._save(db, version)
            return self._present(db, version)

    def revalidate(self, project_id: str, identity: str, request: RunAction) -> ResultVersion:
        version = self.get(project_id, identity)
        self.workspace._current(version.revision, request.expected_revision)
        with self.workspace.connection() as db:
            run = self._current(db, version)
        service = Integrations(self.workspace)
        record = service.prepare(
            project_id,
            run.id,
            IntegrationPrepare(expected_revision=run.revision, author=request.author),
            availability_result=version.id,
        )
        service.refresh_availability(project_id)
        with self.workspace.connection(write=True, project_id=project_id) as db:
            current = self._get(db, project_id, identity)
            self._current(db, current)
            current.availability_id = record.id
            self._save(db, current)
            return self._present(db, current)

    def correct(self, project_id: str, identity: str, request: RunAction) -> ResultVersion:
        """One explicit, capacity-bound worker continuation. Never transfers approval."""
        version = self.get(project_id, identity)
        self.workspace._current(version.revision, request.expected_revision)
        if version.completion != "code" or version.status not in ("blocked", "stale"):
            raise ApplicationError(
                "correction_unavailable",
                "Request correction for a blocked or stale code result.",
                409,
            )
        repository = Path(self.workspace.project(project_id).path)
        integrations = Integrations(self.workspace)
        with gitops.lock(repository):
            settings = integrations.settings(project_id)
            target = integrations.head(project_id)
            with self.workspace.connection() as db:
                run = self._current(db, version)
                task = self.workspace._task(db, project_id, run.task_id)
                self.workspace._require_ready(task)
                if not task.publication or task.publication.target_branch != settings.target_branch:
                    raise ApplicationError(
                        "delivery_target_changed",
                        "Reconcile the task's completion target before correcting its code.",
                        409,
                    )
                workers = self.execution._settings(db, project_id)
                if not workers.selection:
                    raise ApplicationError(
                        "worker_settings_required",
                        "Choose the correction harness and model in project Workers settings.",
                        409,
                    )
                number = self.execution._correction_count(db, project_id, task.id) + 1
                if number > 2:
                    raise ApplicationError(
                        "correction_limit",
                        "Two corrections used. Reconcile outcome and scope with the coordinator.",
                        409,
                    )
            seed, conflicts = gitops.correction_seed(
                repository, target, version.source_commit, uuid4().hex
            )
            evidence = (
                integrations.get(project_id, version.integration_id).model_dump()
                if version.integration_id
                else {}
            )
            correction = Correction(
                result_id=version.id,
                source_commit=version.source_commit,
                target_commit=target,
                base_commit=seed,
                number=number,
                details=json.dumps(
                    {
                        "problem": version.problem,
                        "conflicts": conflicts,
                        "checks": evidence.get("checks", []),
                        "setup": evidence.get("setup_checks", []),
                        "instruction": (
                            "Resolve this task against both inputs. Ask if scope must change; "
                            "do not change Git metadata."
                        ),
                    }
                ),
            )
            with self.workspace.connection(write=True, project_id=project_id) as db:
                current = self._get(db, project_id, identity)
                self.workspace._current(current.revision, request.expected_revision)
                run = self._current(db, current)
                latest_task = self.workspace._task(db, project_id, run.task_id)
                self.workspace._current(latest_task.revision, task.revision)
                self.workspace._require_ready(latest_task)
                if integrations.head(project_id) != target:
                    raise ApplicationError(
                        "target_changed",
                        "Target moved during correction preparation. Inspect it and request again.",
                        409,
                    )
                current.status, current.correction = "changes_requested", correction
                current.feedback = (
                    request.note or "Correct the recorded failure within the published assignment."
                )
                run.status, run.feedback, run.next_correction = (
                    "changes_requested",
                    current.feedback,
                    correction,
                )
                self.execution._save(db, run)
                self._save(db, current)
                self.execution._task_state(
                    db,
                    self.workspace._task(db, project_id, run.task_id),
                    "up_next",
                    f"Requested bounded correction {number}/2 for result v{version.version}.",
                    request.author,
                )
                return self._present(db, current)

    def process(self, project_id: str) -> None:
        """One bounded local operation; called by the service even with worker queue paused."""
        with self.workspace.connection() as db:
            row = db.execute(
                "SELECT id FROM result_versions v WHERE project_id=? "
                "AND status IN ('preparing','delivering') "
                "AND NOT EXISTS (SELECT 1 FROM result_versions n WHERE n.project_id=v.project_id "
                "AND n.task_id=v.task_id AND n.version>v.version) ORDER BY number LIMIT 1",
                (project_id,),
            ).fetchone()
        if not row:
            return
        version = self.get(project_id, row[0])
        try:
            service = Integrations(self.workspace)
            if version.integration_id:
                interrupted = service.get(project_id, version.integration_id)
                if interrupted.status in ("preparing", "applying"):
                    service.recover(interrupted)
                    return
            with self.workspace.connection() as db:
                run = self._current(db, version)
                self.execution._current_assignment(
                    run, self.workspace._task(db, project_id, run.task_id)
                )
            if version.status == "preparing":
                if version.completion == "report":
                    repo = Path(self.workspace.project(project_id).path)
                    if git(repo, "rev-parse", run.base_commit + "^{tree}") != git(
                        repo, "rev-parse", version.source_commit + "^{tree}"
                    ):
                        raise ApplicationError(
                            "report_changes_code",
                            "This report includes code changes. "
                            "Reconcile its completion requirement as code before review.",
                            409,
                        )
                    with self.workspace.connection(write=True, project_id=project_id) as db:
                        current = self._get(db, project_id, version.id)
                        run = self._current(db, current)
                        task = self.workspace._task(db, project_id, run.task_id)
                        self.execution._current_assignment(run, task)
                        if current.status == "preparing":
                            current.candidate_commit = current.source_commit
                            if current.report.outcome == "partial":
                                current.status = "blocked"
                                current.problem_code = "partial_outcome"
                                current.problem = current.report.remaining_work
                            else:
                                current.status, current.completed_at = "delivered", now()
                                # Internal accepted run means a completed report, not code approval.
                                run.status = "accepted"
                                run.accepted_at, run.accepted_by = current.completed_at, "flowfield"
                                self.execution._save(db, run)
                                self.execution._task_state(
                                    db,
                                    task,
                                    "done",
                                    f"Findings delivered in result version {current.version}; "
                                    "completion does not endorse the recommendation.",
                                )
                            self._save(db, current)
                    return
                integration = service.prepare(
                    project_id, run.id, IntegrationPrepare(expected_revision=run.revision)
                )
                with self.workspace.connection(write=True, project_id=project_id) as db:
                    current = self._get(db, project_id, version.id)
                    self._current(db, current)
                    if current.status != "preparing":
                        return
                    current.integration_id = integration.id
                    current.candidate_commit = integration.candidate_commit
                    current.target_before = integration.target_before
                    current.settings_revision = integration.settings_revision
                    current.status = "ready" if integration.status == "ready" else "blocked"
                    current.problem = integration.problem
                    current.problem_code = integration.problem_code
                    if current.status == "ready" and current.report.outcome == "partial":
                        current.status = "blocked"
                        current.problem_code = "partial_outcome"
                        current.problem = current.report.remaining_work
                    self._save(db, current)
            else:
                assert version.integration_id and version.candidate_commit
                integration = service.get(project_id, version.integration_id)
                if integration.status == "applying":
                    integration = service.recover(integration)
                if integration.status in ("ready", "stale"):
                    integration = service.apply(
                        project_id,
                        integration.id,
                        IntegrationApply(
                            expected_revision=integration.revision,
                            candidate_commit=version.candidate_commit,
                            author=version.approved_by or "human",
                        ),
                    )
                if integration.status == "integrated":
                    with self.workspace.connection(write=True, project_id=project_id) as db:
                        self.complete(db, project_id, integration.id)
                else:
                    self._problem(
                        version,
                        integration.problem or "Delivery needs fresh preparation.",
                        stale=True,
                        code=integration.problem_code,
                    )
        except (ApplicationError, OSError) as error:
            if isinstance(error, ApplicationError) and error.code == "integration_busy":
                return  # The service retries queued work after the current local operation.
            self._problem(
                version,
                str(error),
                stale=version.status == "delivering",
                code=error.code if isinstance(error, ApplicationError) else "local_io_failed",
            )

    def _problem(
        self, version: ResultVersion, problem: str, *, stale: bool = False, code: str | None = None
    ) -> None:
        with self.workspace.connection(write=True, project_id=version.project_id) as db:
            current = self._get(db, version.project_id, version.id)
            if current.status in ("preparing", "delivering"):
                current.status = "stale" if stale else "blocked"
                current.problem = problem
                current.problem_code = code
                self._save(db, current)
