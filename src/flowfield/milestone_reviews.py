"""Scoped whole-outcome assessment on canonical verification and delivery records."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from flowfield.adapters import git_integration as gitops
from flowfield.application import now
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.milestone_delivery import MilestoneDeliveries
from flowfield.milestone_promotion import MilestonePromotions
from flowfield.milestone_promotion import read as promotion_read
from flowfield.milestone_verification import (
    current_problem,
    delivered_tasks,
    read,
    save,
    validate_report,
)
from flowfield.milestones import Milestones
from flowfield.questions import QuestionCreate, Questions
from flowfield.review_records import ReviewRecord, ReviewRecords
from flowfield.reviewer_settings import EffectiveReviewer, Reviewers
from flowfield.verification_models import MilestoneVerification, VerificationReport
from flowfield.verification_plans import read as plan_read
from flowfield.worker_settings import occupancy, read_project


class MilestoneVerdict(Record):
    action: Literal["report", "ask_human"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=8000)
    request: str = Field(default="", max_length=8000)
    report: VerificationReport | None = None
    correction_task_id: str | None = None
    correction_check: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode="after")
    def complete(self) -> "MilestoneVerdict":
        if not self.summary.strip() or not self.evidence.strip():
            raise ValueError("State the outcome and its evidence.")
        if self.action == "report" and not self.report:
            raise ValueError("Report every check and full criteria coverage.")
        if (self.action == "ask_human" or self.correction_task_id) and not self.request.strip():
            raise ValueError("State the question or bounded correction.")
        if self.correction_task_id and (self.action != "report" or not self.correction_check):
            raise ValueError("Bind the correction to a failed or unverified check.")
        return self


class MilestoneReview(ReviewRecord):
    verification_id: str
    milestone_id: str
    candidate_commit: str
    reviewer: EffectiveReviewer
    verdict: MilestoneVerdict | None = None
    applied_at: str | None = None


class MilestoneReviews(ReviewRecords[MilestoneReview]):
    record_type = MilestoneReview
    job_table = "milestone_reviews"
    attempt_table = "milestone_review_attempts"

    def request_missing_plan(self, workspace: str, milestone_id: str) -> None:
        from flowfield.delivery_models import MilestoneDelivery

        with self.store.connection(write=True) as db:
            milestone = Milestones.read(db, workspace, milestone_id)
            row = db.execute(
                "SELECT data FROM milestone_deliveries WHERE workspace_id=? AND milestone_id=?",
                (workspace, milestone.id),
            ).fetchone()
            if not row:
                return
            delivery = MilestoneDelivery.model_validate_json(row[0])
            plan = plan_read(db, workspace, milestone.id)
            if (
                not delivery.integration_policy.enabled
                or not read_project(db, delivery.project_id).enabled
                or not Reviewers.resolved(
                    db, workspace, "milestone", entity=milestone.id
                ).effective.enabled
                or (plan and plan.milestone_revision == milestone.revision)
                or delivery.review_plan_question_revision == milestone.revision
            ):
                return
            delivered_tasks(db, workspace, milestone.id, (delivery.project_id, None))
            if delivery.review_plan_question_id:
                questions = Questions(self.store)
                previous = questions._get(db, delivery.project_id, delivery.review_plan_question_id)
                if previous.status in ("open", "answered"):
                    previous = questions._change(
                        previous, previous.revision, "flowfield", status="withdrawn"
                    )
                    questions._save(
                        db,
                        previous,
                        "Milestone criteria changed; the earlier planning question is retained.",
                    )
            question = Questions(self.store)._ask(
                db,
                delivery.project_id,
                QuestionCreate(
                    question=f"{milestone.key}: prepare the combined verification plan"[:200],
                    context="The contributions are delivered, but there is no verification plan "
                    "for the current milestone criteria. The service cannot invent checks "
                    "or evidence.",
                    recommendation="Ask the Coordinator to save or reconcile the whole-outcome "
                    "verification plan. Describe missing intent or access here; saving an answer "
                    "alone does not change the criteria or authorize checks.",
                    author="flowfield",
                ),
            )
            delivery.review_plan_question_id = question.id
            delivery.review_plan_question_revision = milestone.revision
            db.execute(
                "UPDATE milestone_deliveries SET data=? WHERE id=?",
                (delivery.model_dump_json(), delivery.id),
            )

    def _save(self, db: sqlite3.Connection, value: MilestoneReview) -> None:
        super()._save(db, value)
        verification = read(db, value.workspace_id, value.verification_id)
        if verification.review_id != value.id:
            return
        verification.review_status = value.status
        if verification.current:
            if value.status in ("failed", "uncertain", "stopped"):
                verification.status, verification.problem = "failed", value.problem
            elif value.status in ("queued", "preparing", "running", "waiting"):
                verification.status, verification.problem = "verifying", None
        verification.revision += 1
        verification.updated_at = now()
        save(db, verification)

    def prepare(self, workspace: str, milestone_id: str) -> MilestoneVerification | None:
        delivery = MilestoneDeliveries(self.store).get(workspace, milestone_id)
        if not delivery or delivery.status != "ready":
            return None
        with self.store.connection() as db:
            if not read_project(db, delivery.project_id).enabled:
                return None
        with gitops.lock(Path(self.store.project(delivery.project_id).path)):
            with self.store.connection(write=True) as db:
                milestone = Milestones.read(db, workspace, milestone_id)
                plan = plan_read(db, workspace, milestone.id)
                reviewer = (
                    Reviewers(self.store)
                    .resolve(db, workspace, "milestone", entity=milestone.id)
                    .effective
                )
                if (
                    not plan
                    or plan.milestone_revision != milestone.revision
                    or not reviewer.enabled
                    or not reviewer.agent
                    or not read_project(db, delivery.project_id).enabled
                ):
                    return None
                tasks = delivered_tasks(db, workspace, milestone.id, (delivery.project_id, None))
                previous = db.execute(
                    "SELECT data FROM milestone_verifications WHERE workspace_id=? AND "
                    "milestone_id=? "
                    "ORDER BY number DESC LIMIT 1",
                    (workspace, milestone.id),
                ).fetchone()
                if previous:
                    saved = MilestoneVerification.model_validate_json(previous[0])
                    problem = current_problem(db, saved)
                    if not problem and saved.promotion_id and saved.status != "accepted":
                        try:
                            MilestonePromotions(self.store).inputs(
                                promotion_read(db, saved.promotion_id)
                            )
                        except ApplicationError as error:
                            problem = str(error)
                    if not problem:
                        # Failed/stopped evidence needs an owner, never a retry loop.
                        return saved
                    if saved.status in ("promoting", "delivery_blocked"):
                        return saved  # Existing exact promotion retains recovery ownership.
                    saved.current = False
                    if saved.status != "accepted":
                        saved.status = "stale"
                    saved.problem, saved.updated_at = problem, now()
                    saved.revision += 1
                    save(db, saved)
                value = MilestoneVerification(
                    id=uuid4().hex,
                    workspace_id=workspace,
                    milestone_id=milestone.id,
                    milestone_key=milestone.key,
                    milestone_revision=milestone.revision,
                    title=milestone.title,
                    criteria=milestone.acceptance_criteria,
                    checks=plan.checks,
                    instructions=plan.instructions,
                    tasks=tasks,
                    project_id=delivery.project_id,
                    created_at=now(),
                    updated_at=now(),
                    author=plan.author,
                    purpose="review",
                    plan_revision=plan.revision,
                    reviewer=reviewer,
                )
                db.execute(
                    "INSERT INTO "
                    "milestone_verifications(id,workspace_id,milestone_id,status,data) "
                    "VALUES (?,?,?,?,?)",
                    (value.id, workspace, milestone.id, value.status, value.model_dump_json()),
                )
                MilestonePromotions(self.store).create(db, value)
                save(db, value)
                return value

    def enqueue(self, workspace: str, verification_id: str) -> MilestoneReview | None:
        with self.store.connection(write=True) as db:
            value = read(db, workspace, verification_id)
            if value.purpose != "review" or not value.current or not value.promotion_id:
                return None
            existing = db.execute(
                "SELECT data FROM milestone_reviews WHERE verification_id=?", (value.id,)
            ).fetchone()
            if existing:
                return MilestoneReview.model_validate_json(existing[0])
            promotion = promotion_read(db, value.promotion_id)
            if promotion.status not in ("ready", "failed") or not promotion.candidate_commit:
                return None
            assert value.reviewer
            review = MilestoneReview(
                id=uuid4().hex,
                project_id=value.project_id,
                workspace_id=workspace,
                milestone_id=value.milestone_id,
                verification_id=value.id,
                candidate_commit=promotion.candidate_commit,
                reviewer=value.reviewer,
                created_at=now(),
            )
            db.execute(
                "INSERT INTO milestone_reviews(id,project_id,verification_id,status,data) "
                "VALUES (?,?,?,?,?)",
                (review.id, review.project_id, value.id, review.status, review.model_dump_json()),
            )
            value.review_id, value.status, value.problem = review.id, "verifying", None
            value.revision += 1
            save(db, value)
            return review

    def _fresh(self, db: sqlite3.Connection, review: MilestoneReview) -> bool:
        value = read(db, review.workspace_id, review.verification_id)
        problem = current_problem(db, value)
        if not problem and value.promotion_id:
            try:
                promotion = promotion_read(db, value.promotion_id)
                MilestonePromotions(self.store).inputs(promotion)
                if promotion.candidate_commit != review.candidate_commit:
                    problem = "The combined candidate changed. Verify a fresh candidate."
            except ApplicationError as error:
                problem = str(error)
        if not value.current or value.review_id != review.id:
            problem = problem or "A different verification owns this milestone."
        if not problem:
            return True
        review.status, review.problem, review.completed_at = "stale", problem, now()
        self._retire_question(
            db, review, "The milestone review inputs changed; retain this answer as history."
        )
        self._save(db, review)
        return False

    def claim(self, project: str, identity: str, expected_revision: int) -> MilestoneReview:
        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            self.store._current(review.revision, expected_revision)
            if review.status != "queued":
                raise ApplicationError("review_not_queued", "Review no longer owns the queue.", 409)
            if not self._fresh(db, review):
                return review
            settings, slots = read_project(db, project), occupancy(db, project=project)
            if not settings.enabled or slots.active + slots.uncertain >= settings.max_parallel:
                raise ApplicationError(
                    "review_waiting", "Waiting for queue and shared capacity.", 409
                )
            if db.execute(
                "SELECT 1 FROM milestone_reviews WHERE json_extract(data,'$.milestone_id')=? "
                "AND project_id=? AND status IN ('preparing','running','uncertain')",
                (review.milestone_id, project),
            ).fetchone():
                raise ApplicationError(
                    "review_active", "An earlier milestone review still owns cleanup.", 409
                )
            review.status = "preparing"
            self._save(db, review)
            return review

    def finish_attempt(
        self,
        project: str,
        identity: str,
        attempt: str,
        *,
        cleanup_confirmed: bool,
        problem: str | None = None,
    ) -> MilestoneReview:
        with self.store.connection(write=True, project_id=project) as db:
            review = self._get(db, project, identity)
            data = self.attempt(attempt)
            data.update(cleanup_confirmed=cleanup_confirmed, completed_at=now(), problem=problem)
            if review.attempt_id != attempt or review.status not in ("preparing", "running"):
                if (
                    review.attempt_id == attempt
                    and review.status == "uncertain"
                    and cleanup_confirmed
                ):
                    review.status, review.completed_at = "stopped", now()
            elif not cleanup_confirmed:
                review.status, review.problem = (
                    "uncertain",
                    "Review cleanup is unconfirmed; capacity remains reserved.",
                )
            elif problem or not data.get("decision"):
                review.status, review.problem = (
                    "failed",
                    problem or "No structured outcome was submitted. Retry explicitly.",
                )
            elif self._fresh(db, review):
                decision = MilestoneVerdict.model_validate(data["decision"])
                value = read(db, review.workspace_id, review.verification_id)
                if decision.report:
                    validate_report(value, decision.report)
                    value.report, value.limitations = decision.report, decision.evidence
                if decision.action == "ask_human":
                    self._question(db, review, value, decision)
                else:
                    assert decision.report
                    passed = decision.report.coverage == "complete" and all(
                        f.verdict == "passed" for f in decision.report.findings
                    )
                    promotion = (
                        promotion_read(db, value.promotion_id) if value.promotion_id else None
                    )
                    passed = passed and bool(promotion and promotion.status == "ready")
                    value.status, value.problem = (
                        ("ready", None) if passed else ("failed", decision.summary)
                    )
                    review.verdict = decision
                    if passed:
                        if decision.correction_task_id:
                            raise ApplicationError(
                                "invalid_correction",
                                "A passing outcome cannot request a correction.",
                                409,
                            )
                        review.status, review.completed_at, review.applied_at = (
                            "decided",
                            now(),
                            now(),
                        )
                        from flowfield.announcements import publish

                        publish(
                            db,
                            f"workspace:{value.workspace_id}:milestone:{value.id}",
                            None,
                            "work",
                            value.title,
                            "Review milestone",
                            f"/workspaces/{value.workspace_id}/milestones/"
                            f"{value.milestone_key}?verification={value.id}",
                            time.time(),
                        )
                    elif decision.correction_task_id:
                        self._correct(db, review, value, decision)
                        review.status, review.completed_at, review.applied_at = (
                            "decided",
                            now(),
                            now(),
                        )
                    else:
                        self._question(db, review, value, decision)
                value.revision += 1
                value.updated_at = now()
                save(db, value)
            data["status"] = review.status
            db.execute(
                "UPDATE milestone_review_attempts SET data=? WHERE id=?",
                (json.dumps(data), attempt),
            )
            self._save(db, review)
            return review

    def _question(
        self,
        db: sqlite3.Connection,
        review: MilestoneReview,
        value: MilestoneVerification,
        decision: MilestoneVerdict,
    ) -> None:
        question = Questions(self.store)._ask(
            db,
            review.project_id,
            QuestionCreate(
                question=f"{value.milestone_key}: {decision.summary}"[:200],
                context=decision.evidence,
                recommendation=decision.request
                or "Clarify how to resolve the failed or unverified checks with the Coordinator.",
                author="reviewer",
            ),
            origin_review_id=review.id,
        )
        review.question_id, review.status = question.id, "waiting"

    def _correct(
        self,
        db: sqlite3.Connection,
        review: MilestoneReview,
        value: MilestoneVerification,
        decision: MilestoneVerdict,
    ) -> None:
        from flowfield.results import Results

        assert decision.report
        finding = next(
            (f for f in decision.report.findings if f.check == decision.correction_check), None
        )
        contribution = next(
            (
                t
                for t in value.tasks
                if t.task_id == decision.correction_task_id and t.project_id == review.project_id
            ),
            None,
        )
        if not finding or finding.verdict == "passed" or not contribution:
            raise ApplicationError(
                "invalid_correction",
                "Choose a bound contribution and a failed or unverified check.",
                409,
            )
        Results(self.store).milestone_correction(
            db,
            contribution.project_id,
            contribution.result_id,
            contribution.result_revision,
            value.id,
            decision.request,
        )

    def read_context(self, review: MilestoneReview, section: str) -> str:
        with self.store.connection() as db:
            value = read(db, review.workspace_id, review.verification_id)
            if section == "verification":
                return value.model_dump_json()
            if section == "validation":
                return (
                    promotion_read(db, value.promotion_id).model_dump_json()
                    if value.promotion_id
                    else "No promotion evidence."
                )
            if section == "human_answer":
                return (
                    Questions(self.store)
                    ._get(db, review.project_id, review.question_id)
                    .model_dump_json()
                    if review.question_id
                    else "No human answer."
                )
            if section.startswith("contribution:"):
                identity = section.removeprefix("contribution:")
                task = next((t for t in value.tasks if t.task_id == identity), None)
                if task:
                    row = db.execute(
                        "SELECT r.assignment FROM runs r JOIN result_versions v ON "
                        "v.run_id=r.id WHERE v.id=?",
                        (task.result_id,),
                    ).fetchone()
                    return str(row[0])
        if section.startswith("exchanges:"):
            cursor = section.removeprefix("exchanges:")
            if not cursor.isdigit():
                raise ApplicationError("invalid_section", "Use the returned exchange cursor.")
            page = self.attempts(review.project_id, review.id, int(cursor) or None)
            return json.dumps(
                {
                    "items": [
                        {key: item.get(key) for key in ("id", "status", "decision", "problem")}
                        for item in page["items"]
                    ],
                    "next_before": page["next_before"],
                }
            )
        raise ApplicationError(
            "invalid_section", "Read only the bound milestone evidence sections."
        )
