"""One canonical verification owner; report tasks provide evidence, humans accept outcomes."""

import json
import sqlite3
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from uuid import uuid4

from flowfield.application import TaskPublish, TaskRevision, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.milestones import Milestones
from flowfield.reads import PAGE_BYTES, clip, excerpt, page, validate_page
from flowfield.verification_models import (
    DeliveredTask,
    MilestoneTestingNote,
    MilestoneTestingPage,
    MilestoneVerification,
    MilestoneVerificationSummary,
    VerificationPrepare,
    VerificationReport,
    VerificationReview,
)
from flowfield.workspaces import Workspaces


def delivered_tasks(
    db: sqlite3.Connection, workspace_id: str, milestone_id: str, verifier: tuple[str, str | None]
) -> list[DeliveredTask]:
    rows = db.execute(
        "SELECT t.project_id,t.id,t.key,t.data FROM tasks t JOIN workspace_scopes w "
        "ON w.project_id=t.project_id WHERE w.workspace_id=? "
        "AND json_extract(t.data,'$.milestone_id')=? "
        "AND NOT (t.project_id=? AND t.id=?) AND NOT EXISTS ("
        "SELECT 1 FROM milestone_verifications v WHERE v.workspace_id=w.workspace_id "
        "AND v.milestone_id=? AND json_extract(v.data,'$.project_id')=t.project_id "
        "AND json_extract(v.data,'$.task_id')=t.id "
        "AND json_extract(t.data,'$.publication.completion')='report') "
        "ORDER BY t.project_id,t.id LIMIT 1001",
        (workspace_id, milestone_id, verifier[0], verifier[1] or "", milestone_id),
    ).fetchall()
    if not rows or len(rows) > 1000:
        raise ApplicationError(
            "verification_not_ready", "Verification needs 1–1000 contributing tasks.", 409
        )
    values = []
    for row in rows:
        task = json.loads(row["data"])
        if task["status"] != "done":
            raise ApplicationError(
                "verification_not_ready",
                f"{row['key']} must be delivered, including archived work.",
                409,
            )
        saved = db.execute(
            "SELECT v.data,json_extract(r.data,'$.agreement_revision') FROM result_versions v "
            "JOIN runs r ON r.id=v.run_id WHERE v.project_id=? AND v.task_id=? "
            "ORDER BY v.number DESC LIMIT 1",
            (row["project_id"], row["id"]),
        ).fetchone()
        result = json.loads(saved[0]) if saved else None
        if (
            not result
            or result["status"] != "delivered"
            or result["report"]["outcome"] != "complete"
            or saved[1] != task["agreement_revision"]
        ):
            raise ApplicationError(
                "verification_not_ready", f"{row['key']} needs a complete delivered result.", 409
            )
        settings = db.execute(
            "SELECT json_extract(data,'$.revision') FROM integration_settings WHERE project_id=?",
            (row["project_id"],),
        ).fetchone()
        values.append(
            DeliveredTask(
                project_id=row["project_id"],
                task_id=row["id"],
                task_key=row["key"],
                revision=task["revision"],
                agreement_revision=task["agreement_revision"],
                result_id=result["id"],
                result_revision=result["revision"],
                settings_revision=settings[0] if settings else None,
                commit=result.get("candidate_commit") or result["source_commit"],
                output_ids=[
                    file[0]
                    for file in db.execute(
                        "SELECT id FROM output_files WHERE run_id=? ORDER BY number",
                        (result["run_id"],),
                    )
                ]
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='output_files'").fetchone()
                else [],
                target_branch=result.get("target_branch"),
                reconciliation_reason=task.get("reconciliation_reason"),
            )
        )
    return values


def read(db: sqlite3.Connection, workspace_id: str, identity: str) -> MilestoneVerification:
    row = db.execute(
        "SELECT data FROM milestone_verifications WHERE workspace_id=? AND id=?",
        (workspace_id, identity),
    ).fetchone()
    if not row:
        raise ApplicationError("not_found", "Milestone verification not found.", 404)
    return MilestoneVerification.model_validate_json(row[0])


def save(db: sqlite3.Connection, value: MilestoneVerification) -> None:
    db.execute(
        "UPDATE milestone_verifications SET status=?,data=? WHERE id=?",
        (
            value.status,
            value.model_dump_json(exclude={"promotion", "task_titles", "files"}),
            value.id,
        ),
    )


def validate_report(value: MilestoneVerification, report: VerificationReport) -> None:
    available = {identity for task in value.tasks for identity in task.output_ids}
    if (
        len(set(report.output_ids)) != len(report.output_ids)
        or not set(report.output_ids) <= available
    ):
        raise ApplicationError(
            "verification_output_invalid",
            "Select each final file once, from the exact contributing results.",
            409,
        )
    if report.verification_id != value.id or sorted(f.check for f in report.findings) != list(
        range(1, len(value.checks) + 1)
    ):
        raise ApplicationError(
            "verification_evidence_incomplete",
            "Report each prepared check exactly once, using this verification ID.",
            409,
        )
    if any(not f.evidence.strip() for f in report.findings) or not report.coverage_notes.strip():
        raise ApplicationError(
            "verification_evidence_incomplete",
            "Record evidence and explain coverage of the full acceptance criteria.",
            409,
        )


def assignment(
    db: sqlite3.Connection, project_id: str, task_id: str, agreement_revision: int
) -> str:
    row = db.execute(
        "SELECT data FROM milestone_verifications WHERE status='verifying' "
        "AND json_extract(data,'$.project_id')=? AND json_extract(data,'$.task_id')=? "
        "AND json_extract(data,'$.agreement_revision')=? ORDER BY number DESC LIMIT 1",
        (project_id, task_id, agreement_revision),
    ).fetchone()
    return row[0] if row else ""


def current_problem(db: sqlite3.Connection, value: MilestoneVerification) -> str | None:
    milestone = Milestones.read(db, value.workspace_id, value.milestone_id)
    if milestone.revision != value.milestone_revision:
        return (
            f"This report checked milestone revision {value.milestone_revision}; "
            f"revision {milestone.revision} is now current. "
            "Ask the Coordinator to review the changes and verify the updated milestone."
        )
    try:
        tasks = delivered_tasks(
            db, value.workspace_id, value.milestone_id, (value.project_id, value.task_id)
        )
    except ApplicationError as error:
        return str(error)
    resolutions = {item.task_id: item for item in value.dependency_resolutions}
    expected = [
        task.model_copy(
            update={"revision": resolutions[task.task_id].revision, "reconciliation_reason": None}
        )
        if task.project_id == value.project_id and task.task_id in resolutions
        else task
        for task in value.tasks
    ]
    if tasks != expected:
        return "Contributing tasks or delivered results changed. Prepare a new verification."
    if value.purpose == "review":
        from flowfield.reviewer_settings import Reviewers
        from flowfield.verification_plans import read as plan_read

        plan = plan_read(db, value.workspace_id, value.milestone_id)
        if not plan or plan.revision != value.plan_revision:
            return "The verification plan changed. Verify the updated plan."
        reviewer = Reviewers.resolved(
            db, value.workspace_id, "milestone", entity=value.milestone_id
        )
        if value.status != "accepted" and reviewer.effective != value.reviewer:
            return "Milestone reviewer settings changed. A fresh assessment is required."
        return None
    row = db.execute(
        "SELECT data FROM tasks WHERE project_id=? AND id=?", (value.project_id, value.task_id)
    ).fetchone()
    task = json.loads(row[0]) if row else {}
    if (
        task.get("agreement_revision") != value.agreement_revision
        or task.get("milestone_id") != value.milestone_id
        or task.get("archived")
        or (task.get("publication") or {}).get("completion") != "report"
        or (task.get("publication") or {}).get("verification_id") != value.id
    ):
        return "The verifier task changed or was archived. Prepare a new verification."
    if value.result_id:
        latest = db.execute(
            "SELECT id FROM result_versions WHERE project_id=? AND task_id=? "
            "ORDER BY number DESC LIMIT 1",
            (value.project_id, value.task_id),
        ).fetchone()
        if task.get("status") != "done" or not latest or latest[0] != value.result_id:
            return "The verifier was reopened or its result changed. Prepare a new verification."
    return None


def reconcile(db: sqlite3.Connection, project_id: str | None = None) -> None:
    """Reconcile only live evidence in the same transaction as its inputs; receipts survive."""
    for row in db.execute(
        "SELECT data FROM milestone_verifications WHERE status IN "
        "('verifying','ready','accepted') AND json_extract(data,'$.current')=1 "
        "AND (? IS NULL OR workspace_id IN "
        "(SELECT workspace_id FROM workspace_scopes WHERE project_id=?))",
        (project_id, project_id),
    ).fetchall():
        value = MilestoneVerification.model_validate_json(row[0])
        problem = current_problem(db, value)
        previous = value.status
        if problem:
            value.current = False
            value.problem = problem
            if value.status != "accepted":
                value.status = "stale"
        elif value.status == "verifying":
            if value.purpose == "review":
                continue  # The scoped review owner supplies structured findings after cleanup.
            result_row = db.execute(
                "SELECT v.data,r.assignment FROM result_versions v JOIN runs r ON r.id=v.run_id "
                "WHERE v.project_id=? AND v.task_id=? AND v.status='delivered' "
                "ORDER BY v.number DESC LIMIT 1",
                (value.project_id, value.task_id),
            ).fetchone()
            if not result_row:
                continue
            result = json.loads(result_row[0])
            frozen = json.loads(result_row[1]).get("milestone_verification", "")
            if not frozen or json.loads(frozen)["id"] != value.id:
                continue
            value.result_id = result["id"]
            value.summary = result["report"]["summary"]
            value.limitations = result["report"].get("limitations", "")
            report = result["report"].get("verification")
            if report:
                value.report = VerificationReport.model_validate(report)
                try:
                    validate_report(value, value.report)
                except ApplicationError:
                    value.problem = "Verification did not report every prepared check."
            else:
                value.problem = "The report did not include structured verification evidence."
            value.status = (
                "ready"
                if (
                    value.report
                    and not value.problem
                    and value.report.coverage == "complete"
                    and all(f.verdict == "passed" for f in value.report.findings)
                )
                else "failed"
            )
        else:
            continue
        value.revision += 1
        value.updated_at = now()
        save(db, value)
        from flowfield.announcements import publish

        key = f"workspace:{value.workspace_id}:milestone:{value.id}"
        if value.status == "ready" and previous != "ready":
            publish(
                db,
                key,
                None,
                "work",
                value.title,
                "Review milestone",
                f"/workspaces/{value.workspace_id}/milestones/{value.milestone_key}?verification={value.id}",
                time.time(),
            )
        elif previous == "ready":
            db.execute(
                "UPDATE announcement_events SET state='resolved' WHERE "
                "source_key=? AND state='pending'",
                (key,),
            )


class MilestoneVerifications:
    def __init__(self, store: Workspace):
        self.store = store

    def get(self, workspace_id: str, identity: str) -> MilestoneVerification:
        with self.store.connection() as db:
            value = read(db, workspace_id, identity)
            for task in value.tasks:
                row = db.execute(
                    "SELECT json_extract(data,'$.title') FROM task_revisions "
                    "WHERE project_id=? AND task_id=? AND revision=?",
                    (task.project_id, task.task_id, task.revision),
                ).fetchone()
                if row:
                    value.task_titles[task.result_id] = row[0]
            if value.promotion_id:
                from flowfield.milestone_promotion import read as promotion_read

                value.promotion = promotion_read(db, value.promotion_id)
            from flowfield.outputs import Outputs

            selected = value.report.output_ids if value.report else []
            if value.result_id:
                result = db.execute(
                    "SELECT run_id FROM result_versions WHERE id=?", (value.result_id,)
                ).fetchone()
                if result:
                    value.files = Outputs.for_run(db, result[0])
            for identity in selected:
                row = db.execute(
                    "SELECT data FROM output_files WHERE workspace_id=? AND id=?",
                    (workspace_id, identity),
                ).fetchone()
                if row:
                    from flowfield.output_models import OutputFile

                    value.files.append(Outputs._present(db, OutputFile.model_validate_json(row[0])))
            return value

    def testing(self, workspace_id: str, identity: str, offset: int = 0) -> MilestoneTestingPage:
        """Read existing observations only for this exact combined candidate."""
        from flowfield.actors import actor
        from flowfield.milestone_promotion import read as promotion_read
        from flowfield.reply_models import Reply

        if not 0 <= offset <= 10000:
            raise ApplicationError("invalid_request", "Use an offset from 0 to 10000.")
        with self.store.connection() as db:
            value = read(db, workspace_id, identity)
            promotion = promotion_read(db, value.promotion_id) if value.promotion_id else None
            if not promotion or not promotion.candidate_commit:
                return MilestoneTestingPage(items=[])
            # Legacy task observations qualify only when their exact code is also
            # the combined candidate. Never infer milestone scope from their prose.
            rows = db.execute(
                "SELECT r.data,json_extract(t.value,'$.task_key') FROM json_each(?) t "
                "JOIN task_replies r ON r.project_id=json_extract(t.value,'$.project_id') "
                "AND r.task_id=json_extract(t.value,'$.task_id') "
                "WHERE json_extract(r.data,'$.action')='observation' "
                "AND json_extract(r.data,'$.status')='recorded' "
                "AND json_extract(r.data,'$.binding.result_id')="
                "json_extract(t.value,'$.result_id') "
                "AND json_extract(r.data,'$.binding.result_revision')="
                "json_extract(t.value,'$.result_revision') "
                "AND ((json_extract(r.data,'$.milestone.verification_id')=? "
                "AND json_extract(r.data,'$.milestone.candidate_commit')=?) "
                "OR (json_extract(r.data,'$.milestone') IS NULL "
                "AND r.project_id=? AND json_extract(t.value,'$.commit')=?)) "
                "ORDER BY r.created_at DESC,r.id DESC LIMIT 21 OFFSET ?",
                (
                    json.dumps([task.model_dump() for task in value.tasks]),
                    value.id,
                    promotion.candidate_commit,
                    value.project_id,
                    promotion.candidate_commit,
                    offset,
                ),
            ).fetchall()
            notes = []
            for row in rows[:20]:
                reply = Reply.model_validate_json(row[0])
                notes.append(
                    MilestoneTestingNote(
                        id=reply.id,
                        project_id=reply.project_id,
                        task_key=row[1],
                        result_id=reply.binding.result_id or "",
                        body=reply.body,
                        actor=actor(reply.author),
                        created_at=reply.created_at,
                    )
                )
            return MilestoneTestingPage(
                items=notes, next_offset=offset + 20 if len(rows) > 20 else None
            )

    def context(
        self,
        workspace_id: str,
        identity: str,
        *,
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        """Read the complete evidence in bounded, revision-bound JSON text pages."""
        if offset < 0 or not 1 <= limit <= 4000 or (offset and revision is None):
            raise ApplicationError(
                "invalid_request",
                "Use a nonnegative offset, limit 1–4000 and revision for later pages.",
            )
        value = self.get(workspace_id, identity)
        if revision is not None:
            self.store._current(value.revision, revision)
        body = value.model_dump_json()
        if offset > len(body):
            raise ApplicationError("invalid_offset", "Choose an offset within the verification.")
        text = clip(body[offset : offset + limit], PAGE_BYTES - 1000)
        return {
            "id": value.id,
            "revision": value.revision,
            "status": value.status,
            "current": value.current,
            "text": text,
            "offset": offset,
            "total_characters": len(body),
            "next_offset": offset + len(text) if offset + len(text) < len(body) else None,
        }

    def history(
        self, workspace_id: str, milestone_id: str, *, before: int | None = None, limit: int = 20
    ) -> dict[str, Any]:
        validate_page(limit, before)
        with self.store.connection() as db:
            milestone = Milestones.read(db, workspace_id, milestone_id)
            rows = db.execute(
                "SELECT number,json_remove(data,'$.criteria','$.checks','$.tasks','$.report') "
                "FROM milestone_verifications WHERE "
                "workspace_id=? AND milestone_id=? AND (? IS NULL OR number<?) "
                "ORDER BY number DESC LIMIT ?",
                (workspace_id, milestone.id, before, before, limit + 1),
            )
            return page(
                [excerpt({**json.loads(row[1]), "number": row[0]}) for row in rows], limit, "number"
            )

    def prepare(
        self, workspace_id: str, milestone_id: str, request: VerificationPrepare
    ) -> MilestoneVerification:
        from flowfield.adapters import git_integration as gitops
        from flowfield.milestone_delivery import MilestoneDeliveries

        delivery = MilestoneDeliveries(self.store).get(workspace_id, milestone_id)
        boundary = (
            gitops.lock(Path(self.store.project(delivery.project_id).path))
            if delivery
            else nullcontext()
        )
        with boundary:
            return self._prepare(workspace_id, milestone_id, request)

    def _prepare(
        self, workspace_id: str, milestone_id: str, request: VerificationPrepare
    ) -> MilestoneVerification:
        with self.store.connection(write=True) as db:
            milestone = Milestones.read(db, workspace_id, milestone_id)
            self.store._current(milestone.revision, request.expected_revision)
            Workspaces.check_scope(db, workspace_id, request.project_id)
            task = self.store._task(db, request.project_id, request.task_id)
            self.store._current(task.revision, request.expected_task_revision)
            if not milestone.acceptance_criteria.strip():
                raise ApplicationError(
                    "criteria_required",
                    "Set milestone acceptance criteria before verification.",
                    409,
                )
            if (
                task.milestone_id != milestone.id
                or task.status not in ("backlog", "up_next")
                or task.archived
            ):
                raise ApplicationError(
                    "invalid_verifier", "Use an upcoming report task in this milestone.", 409
                )
            if any(not check.strip() or len(check) > 4000 for check in request.checks):
                raise ApplicationError("invalid_request", "Each check needs 1–4000 characters.")
            tasks = delivered_tasks(db, workspace_id, milestone.id, (task.project_id, task.id))
            prerequisites = {(p.project_id, p.id) for p in task.prerequisites}
            missing = [t.task_key for t in tasks if (t.project_id, t.task_id) not in prerequisites]
            if missing:
                raise ApplicationError(
                    "verification_prerequisites_required",
                    "Add all contributing tasks as prerequisites and reconcile the assignment: "
                    + ", ".join(missing),
                    409,
                )
            active = db.execute(
                "SELECT id FROM milestone_verifications WHERE workspace_id=? AND "
                "milestone_id=? AND status IN "
                "('preparing','verifying','ready','promoting','delivery_blocked')",
                (workspace_id, milestone.id),
            ).fetchone()
            if active:
                raise ApplicationError(
                    "verification_exists",
                    "This milestone already has a current verification. Read it "
                    "before preparing another.",
                    409,
                )
            task = self.store._publish_task(
                db,
                task.project_id,
                task.id,
                TaskPublish(
                    expected_revision=task.revision, completion="report", author=request.author
                ),
            )
            value = MilestoneVerification(
                id=uuid4().hex,
                workspace_id=workspace_id,
                milestone_id=milestone.id,
                milestone_key=milestone.key,
                milestone_revision=milestone.revision,
                title=milestone.title,
                criteria=milestone.acceptance_criteria,
                checks=request.checks,
                tasks=tasks,
                project_id=task.project_id,
                task_id=task.id,
                task_key=task.key,
                agreement_revision=task.agreement_revision,
                created_at=now(),
                updated_at=now(),
                author=request.author,
            )
            db.execute(
                "INSERT INTO "
                "milestone_verifications(id,workspace_id,milestone_id,status,data) "
                "VALUES (?,?,?,?,?)",
                (value.id, workspace_id, milestone.id, value.status, value.model_dump_json()),
            )
            from flowfield.milestone_promotion import MilestonePromotions

            MilestonePromotions(self.store).create(db, value)
            save(db, value)
            bound = TaskRevision.model_validate(task.model_dump())
            assert bound.publication
            bound.publication.verification_id = value.id
            bound.revision += 1
            bound.updated_at = value.created_at
            self.store._apply_task(db, bound)
            return value

    def review(
        self, workspace_id: str, identity: str, request: VerificationReview
    ) -> MilestoneVerification:
        from flowfield.adapters import git_integration as gitops
        from flowfield.milestone_promotion import MilestonePromotions

        value = self.get(workspace_id, identity)
        boundary = (
            gitops.lock(Path(self.store.project(value.project_id).path))
            if value.promotion_id
            else nullcontext()
        )
        with boundary:
            if request.action == "accept":
                from flowfield.outputs import Outputs

                for file in value.files:
                    Outputs(self.store).content(file)
            if value.promotion and request.action != "request_changes":
                MilestonePromotions(self.store).inputs(value.promotion)
            self._review(workspace_id, identity, request)
            return self.get(workspace_id, identity)

    def _review(
        self, workspace_id: str, identity: str, request: VerificationReview
    ) -> MilestoneVerification:
        with self.store.connection(write=True) as db:
            value = read(db, workspace_id, identity)
            self.store._current(value.revision, request.expected_revision)
            problem = current_problem(db, value)
            retry = request.action == "retry_promotion"
            expected_status = "delivery_blocked" if retry else "ready"
            if value.status != expected_status or not value.current or problem:
                raise ApplicationError(
                    "verification_not_reviewable",
                    problem or "Only current passing evidence can be reviewed.",
                    409,
                )
            if request.action == "request_changes" and not request.note.strip():
                raise ApplicationError("feedback_required", "Describe the required changes.")
            if value.promotion_id and request.action != "request_changes":
                from flowfield.milestone_promotion import read as promotion_read
                from flowfield.milestone_promotion import save as promotion_save

                promotion = promotion_read(db, value.promotion_id)
                if promotion.status != ("blocked" if retry else "ready"):
                    raise ApplicationError(
                        "promotion_changed", "Read the current promotion before reviewing.", 409
                    )
                if retry and not promotion.approved_at:
                    raise ApplicationError(
                        "approval_required", "This candidate needs human approval.", 409
                    )
                promotion.status = "approved"
                promotion.problem = promotion.problem_code = None
                if not retry:
                    promotion.approved_at, promotion.approved_by = now(), request.author
                promotion_save(db, promotion)
                value.status, value.problem = "promoting", None
            elif retry:
                raise ApplicationError(
                    "promotion_missing", "This milestone has no promotion to retry.", 409
                )
            else:
                value.status = "accepted" if request.action == "accept" else "changes_requested"
            if not retry:
                value.reviewed_by = request.author
                value.reviewed_at = now()
                value.review_note = request.note
            value.revision += 1
            value.updated_at = now()
            save(db, value)
            db.execute(
                "UPDATE announcement_events SET state='resolved' WHERE "
                "source_key=? AND state='pending'",
                (f"workspace:{workspace_id}:milestone:{value.id}",),
            )
            return value


def board_verifications(
    db: sqlite3.Connection, workspace_id: str
) -> dict[str, MilestoneVerificationSummary]:
    """Latest verification identity and counts, without loading report evidence."""
    return {
        row[0]: MilestoneVerificationSummary(
            id=row[1],
            status=row[2] if row[3] else "stale",
            checks_total=row[4],
            checks_passed=row[5],
            review_status=row[6] if row[3] else None,
        )
        for row in db.execute(
            "SELECT milestone_id,id,status,json_extract(data,'$.current'), "
            "json_array_length(data,'$.checks'), "
            "CASE WHEN json_type(data,'$.report')='object' THEN "
            "(SELECT count(*) FROM json_each(v.data,'$.report.findings') "
            "WHERE json_extract(value,'$.verdict')='passed') END, "
            "json_extract(data,'$.review_status') "
            "FROM milestone_verifications v WHERE workspace_id=? AND NOT EXISTS "
            "(SELECT 1 FROM milestone_verifications n WHERE n.workspace_id=v.workspace_id "
            "AND n.milestone_id=v.milestone_id AND n.number>v.number)",
            (workspace_id,),
        )
    }
