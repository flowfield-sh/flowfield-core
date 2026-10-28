"""Shared durable review rounds, human exchanges and cleanup ownership."""

import json
import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Literal
from uuid import uuid4

from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.run_activity import ActivityUpdate, RunActivityPage, update_activity


class ReviewRecord(Record):
    launch_reason: str = "assessment"
    id: str
    workspace_id: str
    revision: int = 1
    status: Literal[
        "queued",
        "preparing",
        "running",
        "waiting",
        "decided",
        "stale",
        "stopped",
        "uncertain",
        "failed",
    ] = "queued"
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    problem: str | None = None
    recipient: Literal["reviewer", "implementer"] = "reviewer"
    attempt_id: str | None = None
    question_id: str | None = None


class ReviewRecords[J: ReviewRecord](ABC):
    record_type: type[J]
    job_table: str
    attempt_table: str

    @abstractmethod
    def _fresh(self, db: sqlite3.Connection, review: J) -> bool:
        raise NotImplementedError

    def __init__(self, store: Workspace):
        self.store = store

    def _get(self, db: sqlite3.Connection, project: str, identity: str) -> J:
        row = db.execute(
            f"SELECT data FROM {self.job_table} WHERE workspace_id=? AND id=?", (project, identity)
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Review job not found in this project.", 404)
        return self.record_type.model_validate_json(row[0])

    def get(self, project: str, identity: str) -> J:
        with self.store.connection() as db:
            return self._get(db, project, identity)

    def _save(self, db: sqlite3.Connection, value: J) -> None:
        value.revision += 1
        db.execute(
            f"UPDATE {self.job_table} SET status=?,data=? WHERE id=?",
            (value.status, value.model_dump_json(), value.id),
        )

    def _retire_question(self, db: sqlite3.Connection, review: J, reason: str) -> None:
        from flowfield.questions import Questions

        if review.question_id:
            owner = Questions(self.store)
            question = owner._get(db, review.workspace_id, review.question_id)
            if question.status in ("open", "answered") and not question.consumed_answer_revision:
                question = owner._change(question, question.revision, "service", status="withdrawn")
                owner._save(db, question, reason)

    def started(self, project: str, identity: str, expected_revision: int) -> J:
        """The execution owner calls this only after observing native startup."""
        with self.store.connection(write=True, workspace_id=project) as db:
            review = self._get(db, project, identity)
            self.store._current(review.revision, expected_revision)
            if review.status != "preparing":
                raise ApplicationError(
                    "review_not_preparing", "This review no longer owns startup.", 409
                )
            review.status, review.started_at = "running", now()
            self._save(db, review)
            return review

    def check(self, project: str, identity: str) -> J:
        """Recheck immediately before a verdict consumer acts; never a delivery grant."""
        with self.store.connection(write=True, workspace_id=project) as db:
            review = self._get(db, project, identity)
            if review.status in ("queued", "waiting", "decided"):
                self._fresh(db, review)
            return review

    def stop(
        self, project: str, identity: str, expected_revision: int, *, cleanup_confirmed: bool
    ) -> J:
        with self.store.connection(write=True, workspace_id=project) as db:
            review = self._get(db, project, identity)
            self.store._current(review.revision, expected_revision)
            if review.status not in ("queued", "preparing", "running", "waiting", "uncertain"):
                raise ApplicationError("review_closed", "This review is already closed.", 409)
            review.status = (
                "stopped"
                if cleanup_confirmed or review.status in ("queued", "waiting")
                else "uncertain"
            )
            review.problem = (
                "Review stopped; retained evidence is unchanged."
                if review.status == "stopped"
                else "Confirm review process cleanup before releasing its slot."
            )
            review.completed_at = now() if review.status == "stopped" else None
            self._retire_question(
                db, review, "Review stopped; the question and answer are retained."
            )
            self._save(db, review)
            return review

    def restart(self) -> None:
        with self.store.connection(write=True) as db:
            for row in db.execute(
                f"SELECT data FROM {self.job_table} WHERE status IN ('preparing','running')"
            ).fetchall():
                review = self.record_type.model_validate_json(row[0])
                review.status, review.problem = (
                    "uncertain",
                    "Service restarted during review. Confirm process cleanup before continuing.",
                )
                self._save(db, review)

    def access(self, project: str, identity: str, attempt: str) -> J:
        with self.store.connection() as db:
            review = self._get(db, project, identity)
            if review.status != "running" or review.attempt_id != attempt:
                raise ApplicationError(
                    "review_closed", "This review round no longer owns execution.", 409
                )
            return review

    def attempts(self, project: str, identity: str, before: int | None = None) -> dict[str, Any]:
        with self.store.connection() as db:
            self._get(db, project, identity)
            rows = db.execute(
                f"SELECT number,data FROM {self.attempt_table} WHERE review_id=? "
                "AND (? IS NULL OR number<?) ORDER BY number DESC LIMIT 21",
                (identity, before, before),
            ).fetchall()
            return {
                "items": [json.loads(row[1]) for row in rows[:20]],
                "next_before": rows[19][0] if len(rows) > 20 else None,
            }

    def save_attempt(self, identity: str, updates: dict[str, Any]) -> None:
        """Patch lifecycle metadata without replacing concurrently recorded evidence."""
        with self.store.connection(write=True, notify=False) as db:
            row = db.execute(
                f"SELECT data FROM {self.attempt_table} WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Review attempt not found.", 404)
            data = json.loads(row[0])
            data.update(updates)
            db.execute(
                f"UPDATE {self.attempt_table} SET data=? WHERE id=?", (json.dumps(data), identity)
            )

    def attempt(self, identity: str) -> dict[str, Any]:
        with self.store.connection() as db:
            row = db.execute(
                f"SELECT data FROM {self.attempt_table} WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Review attempt not found.", 404)
            return dict(json.loads(row[0]))

    def write(self, project: str, attempt: str, updates: list[ActivityUpdate]) -> None:
        """ActivityRecorder storage adapter; the same coalescing and bounds as worker output."""
        with self.store.connection(write=True, notify=False) as db:
            row = db.execute(
                f"SELECT a.data FROM {self.attempt_table} a "
                f"JOIN {self.job_table} r ON r.id=a.review_id "
                "WHERE r.workspace_id=? AND a.id=? AND r.status IN ('preparing','running') "
                "AND json_extract(r.data,'$.attempt_id')=a.id",
                (project, attempt),
            ).fetchone()
            if not row:
                return
            data = json.loads(row[0])
            page = RunActivityPage.model_validate(data.get("activity", {}))
            update_activity(page, updates)
            data["activity"] = page.model_dump()
            db.execute(
                f"UPDATE {self.attempt_table} SET data=? WHERE id=?", (json.dumps(data), attempt)
            )

    def activity(self, project: str, identity: str, attempt: str | None = None) -> RunActivityPage:
        from flowfield.activity_text import action_presentation, preview

        review = self.get(project, identity)
        selected = attempt or review.attempt_id
        with self.store.connection() as db:
            row = db.execute(
                f"SELECT data FROM {self.attempt_table} WHERE review_id=? AND id=?",
                (review.id, selected),
            ).fetchone()
            if attempt and not row:
                raise ApplicationError("not_found", "Attempt not found in this review.", 404)
            data = json.loads(row[0]) if row else {}
        page = RunActivityPage.model_validate(data.get("activity", {}))
        page.active = review.status in ("preparing", "running") and selected == review.attempt_id
        for entry in page.items:
            entry.preview = preview(entry.text, entry.kind)
            entry.abridged = entry.preview != entry.text
            entry.action = action_presentation(entry.text, entry.kind)
        return page

    def begin_attempt(self, project: str, identity: str) -> J:
        with self.store.connection(write=True, workspace_id=project) as db:
            review = self._get(db, project, identity)
            if review.status != "preparing":
                raise ApplicationError("review_closed", "Review no longer owns preparation.", 409)
            review.attempt_id = uuid4().hex
            db.execute(
                f"INSERT INTO {self.attempt_table}(id,review_id,data) VALUES (?,?,?)",
                (
                    review.attempt_id,
                    review.id,
                    json.dumps(
                        {
                            "id": review.attempt_id,
                            "role": review.recipient,
                            "launch_reason": review.launch_reason,
                            "created_at": now(),
                            "status": "preparing",
                            "cleanup_confirmed": True,
                        }
                    ),
                ),
            )
            self._save(db, review)
            return review

    def check_decision_evidence(
        self, db: sqlite3.Connection, review: J, data: dict[str, Any], decision: dict[str, Any]
    ) -> None:
        """Subclass evidence gate, in the same transaction as the decision."""
        return None

    def save_decision(
        self, project: str, identity: str, attempt: str, decision: dict[str, Any]
    ) -> None:
        with self.store.connection(write=True, workspace_id=project) as db:
            review = self._get(db, project, identity)
            if review.status != "running" or review.attempt_id != attempt:
                raise ApplicationError(
                    "review_closed", "Review round no longer owns execution.", 409
                )
            data = self.attempt(attempt)
            if data.get("decision") and data["decision"] != decision:
                raise ApplicationError("review_closed", "The round already has a decision.", 409)
            self.check_decision_evidence(db, review, data, decision)
            data["decision"] = decision
            db.execute(
                f"UPDATE {self.attempt_table} SET data=? WHERE id=?", (json.dumps(data), attempt)
            )

    def resume_answer(self, project: str, identity: str) -> J:
        from flowfield.questions import Questions

        with self.store.connection(write=True, workspace_id=project) as db:
            review = self._get(db, project, identity)
            if review.status != "waiting" or not review.question_id:
                return review
            question = Questions(self.store)._get(db, project, review.question_id)
            if question.status == "withdrawn":
                review.status, review.problem = (
                    "stopped",
                    "Review question withdrawn. Retry explicitly when ready.",
                )
                self._save(db, review)
            elif question.status == "answered" and self._fresh(db, review):
                # Reserve the exact answer once. Revisions preserve its original binding.
                owner = Questions(self.store)
                question = owner._change(
                    question,
                    question.revision,
                    "flowfield",
                    status="applied",
                    consumed_answer_revision=question.revision,
                    decision="Delivered to the scoped review context.",
                )
                owner._save(db, question, "Answer reserved for the review continuation.")
                review.status = "queued"
                review.launch_reason = "answer-continuation"
                self._save(db, review)
            return review

    def retry(self, project: str, identity: str, expected_revision: int) -> J:
        with self.store.connection(write=True, workspace_id=project) as db:
            review = self._get(db, project, identity)
            self.store._current(review.revision, expected_revision)
            if review.status not in ("failed", "stopped"):
                raise ApplicationError(
                    "review_active", "Stop and reconcile this review before retrying.", 409
                )
            if self._fresh(db, review):
                review.launch_reason = "review-retry"
                review.status, review.problem, review.completed_at = "queued", None, None
                self._save(db, review)
            return review
