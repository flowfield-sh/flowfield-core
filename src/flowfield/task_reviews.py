"""Candidate-bound review ownership. Assessment never grants Git delivery authority."""

import hashlib
import json
import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import Record
from flowfield.integration import Integrations
from flowfield.milestones import Milestones
from flowfield.results import Results
from flowfield.reviewer_settings import EffectiveReviewer, Reviewers
from flowfield.worker_settings import occupancy, read_project, workspace_id


class ReviewVerdict(Record):
    action: Literal["approve", "request_changes", "ask_worker", "ask_human"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=8000)
    request: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def require_request(self) -> "ReviewVerdict":
        if not self.summary.strip() or not self.evidence.strip():
            raise ValueError("Describe the conclusion and its evidence.")
        if self.action != "approve" and not self.request.strip():
            raise ValueError("State the correction or question for its recipient.")
        if self.action == "approve" and self.request.strip():
            raise ValueError("Approval cannot contain an unresolved blocking request.")
        return self


class ReviewBinding(Record):
    result_revision: int
    run_id: str
    agreement_revision: int
    candidate_commit: str
    source_commit: str
    target_branch: str
    target_before: str
    integration_id: str
    integration_revision: int
    settings_revision: int
    milestone_id: str | None = None
    milestone_revision: int | None = None
    reviewer: EffectiveReviewer


class TaskReview(Record):
    id: str
    project_id: str
    workspace_id: str
    task_id: str
    result_id: str
    revision: int = 1
    binding: ReviewBinding
    status: Literal[
        "queued", "preparing", "running", "decided", "stale", "stopped", "uncertain"
    ] = "queued"
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    verdict: ReviewVerdict | None = None
    problem: str | None = None


class TaskReviews:
    def __init__(self, store: Workspace):
        self.store = store

    def _get(self, db: sqlite3.Connection, project: str, identity: str) -> TaskReview:
        row = db.execute(
            "SELECT data FROM task_reviews WHERE project_id=? AND id=?", (project, identity)
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Review job not found in this project.", 404)
        return TaskReview.model_validate_json(row[0])

    def get(self, project: str, identity: str) -> TaskReview:
        with self.store.connection() as db:
            return self._get(db, project, identity)

    def _save(self, db: sqlite3.Connection, value: TaskReview) -> None:
        value.revision += 1
        db.execute(
            "UPDATE task_reviews SET status=?,data=? WHERE id=?",
            (value.status, value.model_dump_json(), value.id),
        )

    def _binding(
        self, db: sqlite3.Connection, project: str, result_id: str
    ) -> tuple[str, str, ReviewBinding]:
        results = Results(self.store)
        result = results._get(db, project, result_id)
        run = results._current(db, result)
        task = self.store._task(db, project, result.task_id)
        Execution(self.store)._current_assignment(run, task)
        if (
            result.status != "ready"
            or result.report.outcome != "complete"
            or result.completion != "code"
        ):
            raise ApplicationError(
                "review_not_ready", "Review requires a complete, validated code candidate.", 409
            )
        owner = workspace_id(db, project)
        if not owner:
            raise ApplicationError("review_workspace_required", "Review requires a workspace.", 409)
        reviewer = Reviewers(self.store).resolve(db, owner, "task", project, task.id).effective
        if not reviewer.enabled:
            raise ApplicationError(
                "review_disabled", "Automatic review is disabled for this task.", 409
            )
        if not reviewer.agent:
            raise ApplicationError(
                "reviewer_required",
                "Choose a workspace or task reviewer before scheduling review.",
                409,
            )
        assert result.integration_id
        integrations = Integrations(self.store)
        integration = integrations.get(project, result.integration_id)
        settings = integrations.for_task(project, task.id)
        if (
            integration.status != "ready"
            or integration.candidate_commit != result.candidate_commit
            or settings.revision != result.settings_revision
            or settings.target_branch != result.target_branch
            or integrations.head(project, task.id) != result.target_before
        ):
            raise ApplicationError(
                "review_candidate_changed",
                "Prepare and validate the current destination before review.",
                409,
            )
        milestone = Milestones.read(db, owner, task.milestone_id) if task.milestone_id else None
        prepared = task.publication.milestone if task.publication else None
        if milestone and (
            not prepared or prepared.id != milestone.id or prepared.revision != milestone.revision
        ):
            raise ApplicationError(
                "review_milestone_changed", "Reconcile the changed milestone before review.", 409
            )
        assert result.candidate_commit and result.target_before and result.target_branch
        assert result.settings_revision is not None
        return (
            owner,
            task.id,
            ReviewBinding(
                result_revision=result.revision,
                run_id=run.id,
                agreement_revision=run.agreement_revision,
                candidate_commit=result.candidate_commit,
                source_commit=result.source_commit,
                target_branch=result.target_branch,
                target_before=result.target_before,
                integration_id=integration.id,
                integration_revision=integration.revision,
                settings_revision=result.settings_revision,
                milestone_id=milestone.id if milestone else None,
                milestone_revision=milestone.revision if milestone else None,
                reviewer=reviewer,
            ),
        )

    def prepare(self, project: str, result_id: str) -> TaskReview:
        """Explicit service entry; does not scan old results or start a model."""
        with self.store.connection(write=True, project_id=project) as db:
            owner, task, binding = self._binding(db, project, result_id)
            digest = hashlib.sha256(binding.model_dump_json().encode()).hexdigest()
            existing = db.execute(
                "SELECT data FROM task_reviews WHERE result_id=? AND binding=?", (result_id, digest)
            ).fetchone()
            if existing:
                return TaskReview.model_validate_json(existing[0])
            review = TaskReview(
                id=uuid4().hex,
                project_id=project,
                workspace_id=owner,
                task_id=task,
                result_id=result_id,
                binding=binding,
                created_at=now(),
            )
            db.execute(
                "INSERT INTO task_reviews(id,project_id,task_id,result_id,binding,status,data) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    review.id,
                    project,
                    task,
                    result_id,
                    digest,
                    review.status,
                    review.model_dump_json(),
                ),
            )
            return review

    def _fresh(self, db: sqlite3.Connection, review: TaskReview) -> bool:
        try:
            owner, task, current = self._binding(db, review.project_id, review.result_id)
            if (
                owner == review.workspace_id
                and task == review.task_id
                and current == review.binding
            ):
                return True
            problem = "Review settings or candidate evidence changed. Prepare a fresh review."
        except ApplicationError as error:
            problem = error.message
        review.status, review.problem, review.completed_at = "stale", problem, now()
        self._save(db, review)
        return False

    def claim(self, project: str, identity: str, expected_revision: int) -> TaskReview:
        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            self.store._current(review.revision, expected_revision)
            if review.status != "queued":
                raise ApplicationError(
                    "review_not_queued", "This review is already claimed or closed.", 409
                )
            if not self._fresh(db, review):
                return review
            settings = read_project(db, project)
            slots = occupancy(db, project=project)
            if not settings.enabled or slots.active + slots.uncertain >= settings.max_parallel:
                raise ApplicationError(
                    "review_waiting",
                    "Review is waiting for the queue and shared worker capacity.",
                    409,
                )
            if db.execute(
                "SELECT 1 FROM task_reviews WHERE project_id=? AND task_id=? "
                "AND status IN ('preparing','running','uncertain')",
                (project, review.task_id),
            ).fetchone():
                raise ApplicationError(
                    "review_active", "This task already has a review owner.", 409
                )
            review.status = "preparing"
            self._save(db, review)
            return review

    def started(self, project: str, identity: str, expected_revision: int) -> TaskReview:
        """The execution owner calls this only after observing native startup."""
        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            self.store._current(review.revision, expected_revision)
            if review.status != "preparing":
                raise ApplicationError(
                    "review_not_preparing", "This review no longer owns startup.", 409
                )
            review.status, review.started_at = "running", now()
            self._save(db, review)
            return review

    def conclude(
        self,
        project: str,
        identity: str,
        expected_revision: int,
        verdict: ReviewVerdict,
        *,
        cleanup_confirmed: bool,
    ) -> TaskReview:
        """Persist a structured assessment only; downstream application must revalidate it."""
        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            if review.status == "decided" and review.verdict == verdict:
                return review  # An exact replay cannot create another decision or delivery.
            self.store._current(review.revision, expected_revision)
            if review.status != "running":
                raise ApplicationError(
                    "review_closed", "This review no longer owns an active attempt.", 409
                )
            if not cleanup_confirmed:
                review.status, review.problem = (
                    "uncertain",
                    "Review finished without confirmed process cleanup. No verdict was applied.",
                )
                self._save(db, review)
                return review
            if not self._fresh(db, review):
                return review
            review.status, review.verdict, review.completed_at = "decided", verdict, now()
            self._save(db, review)
            return review

    def check(self, project: str, identity: str) -> TaskReview:
        """Recheck immediately before a verdict consumer acts; never a delivery grant."""
        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            if review.status in ("queued", "decided"):
                self._fresh(db, review)
            return review

    def stop(
        self, project: str, identity: str, expected_revision: int, *, cleanup_confirmed: bool
    ) -> TaskReview:
        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            self.store._current(review.revision, expected_revision)
            if review.status not in ("queued", "preparing", "running", "uncertain"):
                raise ApplicationError("review_closed", "This review is already closed.", 409)
            review.status = (
                "stopped" if cleanup_confirmed or review.status == "queued" else "uncertain"
            )
            review.problem = (
                "Review stopped; retained evidence is unchanged."
                if review.status == "stopped"
                else "Confirm review process cleanup before releasing its slot."
            )
            review.completed_at = now() if review.status == "stopped" else None
            self._save(db, review)
            return review

    def restart(self) -> None:
        with self.store.connection(write=True) as db:
            for row in db.execute(
                "SELECT data FROM task_reviews WHERE status IN ('preparing','running')"
            ).fetchall():
                review = TaskReview.model_validate_json(row[0])
                review.status, review.problem = (
                    "uncertain",
                    "Service restarted during review. Confirm process cleanup before continuing.",
                )
                self._save(db, review)

    def context(
        self, project: str, identity: str, section: Literal["assignment", "result", "validation"]
    ) -> str:
        """Retained source evidence; the transport owner pages this before exposing it."""
        review = self.get(project, identity)
        if section == "assignment":
            return json.dumps(
                Execution(self.store).assignment(project, review.binding.run_id), ensure_ascii=False
            )
        if section == "validation":
            return (
                Integrations(self.store)
                .get(project, review.binding.integration_id)
                .model_dump_json()
            )
        with self.store.connection() as db:
            return Results(self.store)._get(db, project, review.result_id).report.model_dump_json()
