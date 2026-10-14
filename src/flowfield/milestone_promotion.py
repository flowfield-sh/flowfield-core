"""Combined candidate validation and durable exact milestone promotion."""

import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from flowfield.adapters import git_checkout
from flowfield.adapters import git_integration as gitops
from flowfield.application import Workspace, now
from flowfield.candidate_validation import validate
from flowfield.errors import ApplicationError
from flowfield.integration import Integrations
from flowfield.integration_models import DELIVERY_BLOCKERS
from flowfield.milestone_delivery import for_milestone
from flowfield.promotion_models import MilestonePromotion
from flowfield.verification_models import MilestoneVerification, VerificationStatus


def read(db: sqlite3.Connection, identity: str) -> MilestonePromotion:
    row = db.execute("SELECT data FROM milestone_promotions WHERE id=?", (identity,)).fetchone()
    if not row:
        raise ApplicationError("not_found", "Milestone promotion not found.", 404)
    return MilestonePromotion.model_validate_json(row[0])


def save(db: sqlite3.Connection, value: MilestonePromotion) -> None:
    db.execute(
        "UPDATE milestone_promotions SET status=?,data=? WHERE id=?",
        (value.status, value.model_dump_json(), value.id),
    )


class MilestonePromotions:
    def __init__(self, store: Workspace):
        self.store = store
        self.integrations = Integrations(store)

    def create(self, db: sqlite3.Connection, verification: MilestoneVerification) -> None:
        delivery = for_milestone(db, verification.project_id, verification.milestone_id)
        if not delivery:
            return
        repository = Path(self.store._project(db, delivery.project_id).path)
        settings = self.integrations._settings(db, delivery.project_id)
        if settings.target_branch != delivery.destination_branch:
            raise ApplicationError(
                "delivery_target_changed",
                "The project destination changed since this milestone was planned. "
                "Restore its intended destination before preparing promotion.",
                409,
            )
        value = MilestonePromotion(
            id=uuid4().hex,
            workspace_id=verification.workspace_id,
            milestone_id=verification.milestone_id,
            verification_id=verification.id,
            project_id=delivery.project_id,
            source_branch=delivery.branch,
            source_commit=gitops.target(repository, delivery.branch),
            target_branch=delivery.destination_branch,
            target_before=gitops.target(repository, delivery.destination_branch),
            checkout=git_checkout.binding(repository),
            settings_revision=settings.revision,
            created_at=now(),
        )
        verification.promotion_id, verification.status = value.id, "preparing"
        # Verification is inserted first to retain the foreign-key boundary.
        db.execute(
            "INSERT INTO milestone_promotions VALUES (?,?,?,?,?)",
            (
                value.id,
                value.project_id,
                value.verification_id,
                value.status,
                value.model_dump_json(),
            ),
        )

    def inputs(self, value: MilestonePromotion, *, applied: bool = False) -> None:
        from flowfield.milestone_verification import current_problem
        from flowfield.milestone_verification import read as verification_read

        with self.store.connection() as db:
            verification = verification_read(db, value.workspace_id, value.verification_id)
            problem = current_problem(db, verification)
            settings = self.integrations._settings(db, value.project_id)
        if problem:
            raise ApplicationError("verification_changed", problem, 409)
        repository = Path(self.store.project(value.project_id).path)
        if (
            settings.revision != value.settings_revision
            or settings.target_branch != value.target_branch
        ):
            raise ApplicationError(
                "settings_changed", "Project settings changed. Verify a new candidate.", 409
            )
        if git_checkout.binding(repository) != value.checkout:
            raise ApplicationError(
                "delivery_destination_changed",
                "Destination checkout changed. Verify a new candidate.",
                409,
            )
        if gitops.target(repository, value.source_branch) != value.source_commit:
            raise ApplicationError(
                "source_changed", "Milestone work changed. Verify a new candidate.", 409
            )
        expected = value.candidate_commit if applied else value.target_before
        if gitops.target(repository, value.target_branch) != expected:
            raise ApplicationError(
                "target_changed", "Project destination moved. Verify the new combination.", 409
            )

    def _save(self, value: MilestonePromotion) -> None:
        from flowfield.milestone_verification import read as verification_read
        from flowfield.milestone_verification import save as verification_save

        with self.store.connection(write=True) as db:
            save(db, value)
            verification = verification_read(db, value.workspace_id, value.verification_id)
            previous = verification.status
            states: dict[str, VerificationStatus] = {
                "ready": "verifying",
                "approved": "promoting",
                "applying": "promoting",
                "delivered": "accepted",
                "blocked": "delivery_blocked",
                "failed": "failed",
                "stale": "stale",
            }
            status = states.get(value.status)
            # A ready candidate must not rewind an already completed verifier.
            if value.status == "ready" and verification.status != "preparing":
                status = None
            if status:
                verification.status = status
                verification.problem = value.problem
                if status == "stale":
                    verification.current = False
                key = f"workspace:{value.workspace_id}:milestone:{value.verification_id}"
                if status == "delivery_blocked" and previous != status:
                    from flowfield.announcements import publish

                    publish(
                        db,
                        key,
                        None,
                        "work",
                        verification.title,
                        "Retry milestone promotion",
                        f"/workspaces/{value.workspace_id}/milestones/{verification.milestone_key}"
                        f"?verification={verification.id}",
                        time.time(),
                    )
                elif status == "stale":
                    db.execute(
                        "UPDATE announcement_events SET state='resolved' "
                        "WHERE source_key=? AND state='pending'",
                        (key,),
                    )
            verification.revision += 1
            verification.updated_at = now()
            verification_save(db, verification)

    def prepare(self, value: MilestonePromotion) -> None:
        repository = Path(self.store.project(value.project_id).path)
        with gitops.lock(repository):
            try:
                self.inputs(value)
                if value.validation_started_at:
                    raise ApplicationError(
                        "validation_interrupted",
                        "Candidate validation was interrupted. Files were preserved; "
                        "ask the Coordinator to prepare a new verification.",
                        409,
                    )
                value.validation_started_at = now()
                self._save(value)
                value.candidate_commit = gitops.candidate(
                    repository, value.target_before, value.source_commit, value.id
                )
                evidence = validate(
                    self.store.directory / "promotion-work",
                    repository,
                    value.id,
                    value.candidate_commit,
                    self.integrations.settings(value.project_id),
                )
                value.workspace = evidence.workspace
                value.setup_checks, value.checks = evidence.setup_checks, evidence.checks
                if evidence.problem:
                    raise ApplicationError(
                        evidence.problem_code or "validation_failed", evidence.problem, 409
                    )
                self.inputs(value)
                value.status = "ready"
            except (ApplicationError, OSError) as error:
                value.status, value.problem = "failed", str(error)
                value.problem_code = (
                    error.code if isinstance(error, ApplicationError) else "validation_failed"
                )
            self._save(value)

    def apply(self, value: MilestonePromotion) -> None:
        repository = Path(self.store.project(value.project_id).path)
        with gitops.lock(repository):
            with self.store.connection() as db:
                value = read(db, value.id)
            if value.status not in ("approved", "applying") or not value.approved_at:
                return
            try:
                applied = bool(
                    value.apply_started_at
                    and gitops.target(repository, value.target_branch) == value.candidate_commit
                )
                self.inputs(value, applied=applied)
                assert value.candidate_commit
                if applied:
                    git_checkout.verify(
                        repository, value.target_branch, value.candidate_commit, value.checkout
                    )
                else:
                    git_checkout.verify(
                        repository, value.target_branch, value.target_before, value.checkout
                    )
                    value.status, value.apply_started_at = "applying", now()
                    self._save(value)
                    # Recheck canonical intent while the short filesystem transaction runs.
                    with self.store.connection(write=True) as db:
                        from flowfield.milestone_verification import current_problem
                        from flowfield.milestone_verification import read as verification_read

                        verification = verification_read(
                            db, value.workspace_id, value.verification_id
                        )
                        problem = current_problem(db, verification)
                        if problem or verification.status != "promoting":
                            raise ApplicationError(
                                "verification_changed", problem or "Promotion was superseded.", 409
                            )
                        gitops.apply(
                            repository,
                            value.target_branch,
                            value.target_before,
                            value.candidate_commit,
                            value.checkout,
                        )
                value.status, value.delivered_at = "delivered", now()
                value.problem = value.problem_code = None
            except (ApplicationError, OSError) as error:
                value.problem = str(error)
                value.problem_code = (
                    error.code if isinstance(error, ApplicationError) else "delivery_uncertain"
                )
                value.status = "blocked" if value.problem_code in DELIVERY_BLOCKERS else "stale"
            self._save(value)

    def process(self, project_id: str) -> None:
        # One deterministic expensive operation per project per service tick.
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT p.data FROM milestone_promotions p "
                "JOIN milestone_verifications v ON v.id=p.verification_id WHERE p.project_id=? "
                "AND (p.status IN ('preparing','approved','applying') "
                "OR (p.status IN ('ready','blocked') "
                "AND v.status IN ('verifying','ready','delivery_blocked'))) "
                "ORDER BY CASE WHEN p.status IN ('ready','blocked') THEN 1 ELSE 0 END, "
                "p.rowid LIMIT 50",
                (project_id,),
            ).fetchall()
        for row in rows:
            value = MilestonePromotion.model_validate_json(row[0])
            if value.status == "preparing":
                self.prepare(value)
                return
            if value.status in ("approved", "applying"):
                self.apply(value)
                return
            try:
                self.inputs(value)
            except (ApplicationError, OSError) as error:
                value.status, value.problem = "stale", str(error)
                value.problem_code = (
                    error.code if isinstance(error, ApplicationError) else "source_unavailable"
                )
                self._save(value)
