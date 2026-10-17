"""Candidate-bound review ownership. Assessment never grants Git delivery authority."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from flowfield.adapters import git_integration as gitops
from flowfield.agent_models import AgentChoice
from flowfield.application import now
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import Record
from flowfield.integration import Integrations
from flowfield.milestone_delivery import integration_enabled, workspace_policy
from flowfield.milestones import Milestones
from flowfield.results import Results
from flowfield.review_records import ReviewRecord, ReviewRecords
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
    assignment_digest: str = ""
    delivery_id: str | None = None
    integration_policy_revision: int | None = None
    workspace_integration_policy_revision: int | None = None


class TaskReview(ReviewRecord):
    task_id: str
    result_id: str
    binding: ReviewBinding
    verdict: ReviewVerdict | None = None
    applied_at: str | None = None
    integration_applied_at: str | None = None


class ReviewAttemptInfo(Record):
    id: str
    role: str
    created_at: str
    completed_at: str | None = None
    status: str
    cleanup_confirmed: bool
    checkout: str | None = None
    pid: int | None = None
    process_stamp: str | None = None
    requested_agent: AgentChoice | None = None
    applied_agent: AgentChoice | None = None
    problem: str | None = None


class ReviewAttemptPage(Record):
    items: list[ReviewAttemptInfo]
    next_before: int | None = None


class TaskReviews(ReviewRecords[TaskReview]):
    record_type = TaskReview
    job_table = "task_reviews"
    attempt_table = "review_attempts"

    def _binding(
        self, db: sqlite3.Connection, project: str, result_id: str, *, delivering: bool = False
    ) -> tuple[str, str, ReviewBinding]:
        results = Results(self.store)
        result = results._get(db, project, result_id)
        run = results._current(db, result)
        task = self.store._task(db, project, result.task_id)
        Execution(self.store)._current_assignment(run, task)
        if (
            result.status != ("delivering" if delivering else "ready")
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
            integration.status not in (("ready", "applying", "stale") if delivering else ("ready",))
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
        from flowfield.milestone_delivery import for_milestone

        delivery = for_milestone(db, project, task.milestone_id)
        prepared = task.publication.milestone if task.publication else None
        if milestone and (
            not prepared or prepared.id != milestone.id or prepared.revision != milestone.revision
        ):
            raise ApplicationError(
                "review_milestone_changed", "Reconcile the changed milestone before review.", 409
            )
        assert result.candidate_commit and result.target_before and result.target_branch
        assert result.source_commit
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
                assignment_digest=hashlib.sha256(
                    json.dumps(
                        Execution(self.store).assignment(project, run.id), sort_keys=True
                    ).encode()
                ).hexdigest(),
                delivery_id=delivery.id if delivery else None,
                workspace_integration_policy_revision=workspace_policy(db, owner).revision
                if delivery and delivery.integration_policy.enabled is None
                else None,
                integration_policy_revision=delivery.integration_policy.revision
                if delivery
                else None,
            ),
        )

    def delegate(self, project: str, identity: str) -> TaskReview:
        """Apply one current verdict under separately human-granted branch authority."""
        from flowfield.milestone_delivery import for_milestone
        from flowfield.result_models import ResultReview

        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            if (
                review.integration_applied_at
                or review.status != "decided"
                or not review.verdict
                or review.verdict.action != "approve"
            ):
                return review
            if not self._fresh(db, review):
                return review
            delivery = for_milestone(db, project, review.binding.milestone_id)
            if (
                not delivery
                or not integration_enabled(db, delivery)
                or not read_project(db, project).enabled
            ):
                return review
            Results(self.store)._review(
                db,
                project,
                review.result_id,
                ResultReview(
                    expected_revision=review.binding.result_revision,
                    candidate_commit=review.binding.candidate_commit,
                    action="approve",
                    author="flowfield",
                    note=(
                        f"Reviewed integration delegated to {delivery.branch} "
                        f"under policy revision {delivery.integration_policy.revision}."
                    ),
                ),
                delegated_review_id=review.id,
            )
            review.integration_applied_at = now()
            self._save(db, review)
            return review

    def refresh_destination(self, project: str, result_id: str) -> bool:
        """A sibling delivery requires a newly assembled, validated and reviewed candidate."""
        from flowfield.execution_models import RunAction
        from flowfield.milestone_delivery import for_milestone

        results = Results(self.store)
        with self.store.connection() as db:
            result = results._get(db, project, result_id)
            task = self.store._task(db, project, result.task_id)
            delivery = for_milestone(db, project, task.milestone_id)
            if (
                not delivery
                or not integration_enabled(db, delivery)
                or not read_project(db, project).enabled
                or result.status not in ("ready", "stale")
                or (result.status == "stale" and result.problem_code != "target_changed")
                or result.target_branch != delivery.branch
                or Integrations(self.store)._settings(db, project).revision
                != result.settings_revision
                or gitops.target(Path(self.store._project(db, project).path), delivery.branch)
                == result.target_before
                or db.execute(
                    "SELECT 1 FROM task_reviews WHERE project_id=? AND task_id=? "
                    "AND status IN ('preparing','running','waiting','uncertain')",
                    (project, task.id),
                ).fetchone()
            ):
                return False
        results.reprepare(project, result_id, RunAction(expected_revision=result.revision))
        return True

    def validate_delivery(self, db: sqlite3.Connection, project: str, result_id: str) -> None:
        """Recheck authority inside the Git owner's final transaction, before ref update."""
        from pathlib import Path

        from flowfield.adapters.git_workspace import git
        from flowfield.milestone_delivery import for_milestone

        result = Results(self.store)._get(db, project, result_id)
        if not result.delegated_review_id:
            return
        review = self._get(db, project, result.delegated_review_id)
        _, _, binding = self._binding(db, project, result_id, delivering=True)
        # Approval and checkout-only recovery advance these operation revisions, not evidence.
        comparable = binding.model_copy(
            update={
                "result_revision": review.binding.result_revision,
                "integration_revision": review.binding.integration_revision,
            }
        )
        delivery = for_milestone(db, project, review.binding.milestone_id)
        if (
            comparable != review.binding
            or not review.integration_applied_at
            or review.status != "decided"
            or not review.verdict
            or review.verdict.action != "approve"
            or not delivery
            or not integration_enabled(db, delivery)
            or delivery.branch != result.target_branch
        ):
            raise ApplicationError(
                "delegation_changed",
                "Delegated integration authority or review inputs changed. "
                "Prepare a fresh candidate and decision.",
                409,
            )
        marker = (
            git(
                Path(self.store.project(project).path),
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/flowfield/milestones/{delivery.id}",
                allowed_returncodes=(0, 1),
            )
            .decode()
            .strip()
        )
        if marker != delivery.base_commit:
            raise ApplicationError(
                "delegation_changed",
                "Milestone branch ownership changed; inspect the repository.",
                409,
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
            if db.execute(
                "SELECT 1 FROM task_reviews WHERE project_id=? AND task_id=? "
                "AND status IN ('preparing','running','uncertain') LIMIT 1",
                (project, task),
            ).fetchone():
                raise ApplicationError(
                    "review_active", "The previous review still owns execution or cleanup.", 409
                )
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
        self._retire_question(db, review, "The review inputs changed; this question is outdated.")
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

    def latest(self, project: str, task: str) -> TaskReview | None:
        with self.store.connection() as db:
            task = self.store._task(db, project, task).id
            row = db.execute(
                "SELECT data FROM task_reviews WHERE project_id=? AND task_id=? "
                "ORDER BY number DESC LIMIT 1",
                (project, task),
            ).fetchone()
            return TaskReview.model_validate_json(row[0]) if row else None

    def finish_attempt(
        self,
        project: str,
        identity: str,
        attempt: str,
        *,
        cleanup_confirmed: bool,
        problem: str | None = None,
    ) -> TaskReview:
        from flowfield.questions import QuestionCreate, Questions
        from flowfield.result_models import ResultReview

        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            if review.attempt_id != attempt or review.status not in ("preparing", "running"):
                if review.attempt_id == attempt:
                    data = self.attempt(attempt)
                    data.update(
                        status=review.status,
                        cleanup_confirmed=cleanup_confirmed,
                        completed_at=now(),
                        problem=problem or review.problem,
                    )
                    db.execute(
                        "UPDATE review_attempts SET data=? WHERE id=?", (json.dumps(data), attempt)
                    )
                return review
            data = self.attempt(attempt)
            data.update(cleanup_confirmed=cleanup_confirmed, completed_at=now(), problem=problem)
            decision = data.get("decision")
            if not cleanup_confirmed:
                review.status, review.problem = (
                    "uncertain",
                    "Review process cleanup is unconfirmed; its slot remains reserved.",
                )
            elif problem or not decision:
                review.status, review.problem = (
                    "failed",
                    problem or "The agent ended without a structured review. Retry explicitly.",
                )
            elif not self._fresh(db, review):
                pass
            else:
                action = decision["action"]
                # Application and receipt share a transaction. Native agents never deliver Git.
                if action == "ask_human":
                    question = Questions(self.store)._ask(
                        db,
                        project,
                        QuestionCreate(
                            task_id=review.task_id,
                            question=decision["summary"][:200],
                            context=decision["evidence"],
                            recommendation=decision["request"],
                            author="reviewer" if review.recipient == "reviewer" else "worker",
                        ),
                        origin_review_id=review.id,
                    )
                    review.question_id, review.status = question.id, "waiting"
                elif action in ("ask_worker", "answer"):
                    review.recipient = "implementer" if action == "ask_worker" else "reviewer"
                    review.status = "queued"
                else:
                    review.verdict = ReviewVerdict.model_validate(decision)
                    review.status, review.completed_at = "decided", now()
                    self._save(db, review)
                    if action == "request_changes":
                        Results(self.store)._review(
                            db,
                            project,
                            review.result_id,
                            ResultReview(
                                expected_revision=review.binding.result_revision,
                                candidate_commit=review.binding.candidate_commit,
                                action="request_changes",
                                note=decision["request"],
                                author="reviewer",
                            ),
                        )
                    review.applied_at = now()
                self.store._insert_activity(
                    db,
                    project,
                    review.task_id,
                    attempt,
                    "note",
                    decision["summary"]
                    + "\n\n"
                    + decision["evidence"]
                    + ("\n\n" + decision["request"] if decision.get("request") else ""),
                    "reviewer" if data["role"] == "reviewer" else "worker",
                    now(),
                )
            data["status"] = review.status
            db.execute("UPDATE review_attempts SET data=? WHERE id=?", (json.dumps(data), attempt))
            self._save(db, review)
            return review

    def read_context(self, review: TaskReview, section: str) -> str:
        if section.startswith("exchanges:"):
            try:
                before = int(section.removeprefix("exchanges:")) or None
            except ValueError:
                raise ApplicationError(
                    "invalid_cursor", "Use exchanges:0 or the returned cursor."
                ) from None
            page = self.attempts(review.project_id, review.id, before)
            return json.dumps(
                {
                    "items": [
                        {
                            "section": "exchange:" + item["id"],
                            "role": item["role"],
                            "status": item.get("status"),
                        }
                        for item in page["items"]
                    ],
                    "next_before": page["next_before"],
                }
            )
        if section == "result" or section == "validation":
            return self.context(
                review.project_id, review.id, "result" if section == "result" else "validation"
            )
        if section.startswith("exchange:"):
            # Individual pages prevent a growing exchange transcript in every prompt.
            identity = section.removeprefix("exchange:")
            with self.store.connection() as db:
                row = db.execute(
                    "SELECT data FROM review_attempts WHERE review_id=? AND id=?",
                    (review.id, identity),
                ).fetchone()
                if row:
                    return json.dumps(json.loads(row[0]).get("decision"), ensure_ascii=False)
            raise ApplicationError("not_found", "Exchange not found in this review.", 404)
        if section == "human_answer":
            from flowfield.questions import Questions

            if review.question_id:
                question = Questions(self.store).get(review.project_id, review.question_id)
                return question.model_dump_json()
            return "No human answer."
        return Execution(self.store).context_section(
            review.project_id, review.binding.run_id, section
        )
