"""Guidance freshness and guarded reconciliation, using the shared review lifecycle."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from flowfield.application import Task, TaskRevision, Workspace, now
from flowfield.document_models import DocumentReference, DocumentSnapshot
from flowfield.document_usage import DocumentUsage
from flowfield.errors import ApplicationError
from flowfield.guidance_models import GuidanceBinding, GuidanceDecision
from flowfield.publication import Publication
from flowfield.reviewer_settings import Reviewers
from flowfield.worker_documents import sections, select

if TYPE_CHECKING:
    from flowfield.result_models import ResultVersion
    from flowfield.task_reviews import TaskReview, TaskReviews


@dataclass
class GuidanceInputs:
    workspace_id: str
    project_id: str | None
    status: str
    archived: bool
    documents: list[DocumentReference]
    publication: Publication | None


def pending_task(
    store: Workspace,
    db: sqlite3.Connection,
    workspace: str,
    task: str,
    result: "ResultVersion | None" = None,
) -> bool:
    """Body-free freshness for collection projections; never read task history."""
    row = db.execute(
        "SELECT project_id,json_extract(data,'$.status'),json_extract(data,'$.archived'),"
        "json_extract(data,'$.documents'),json_extract(data,'$.publication') FROM tasks "
        "WHERE workspace_id=? AND (id=? OR key=? COLLATE NOCASE)",
        (workspace, task, task),
    ).fetchone()
    if not row or row[1] == "done" or row[2] or not row[4]:
        return False
    value = GuidanceInputs(
        workspace,
        row[0],
        row[1],
        bool(row[2]),
        [DocumentReference.model_validate(ref) for ref in json.loads(row[3] or "[]")],
        Publication.model_validate_json(row[4]),
    )
    return pending(store, db, value, result)


def selected_references(
    store: Workspace, db: sqlite3.Connection, task: TaskRevision | GuidanceInputs
) -> list[DocumentReference]:
    defaults = (
        store._project(db, task.project_id).worker_documents
        if task.project_id
        else DocumentUsage.projectless_defaults(db, task.workspace_id)
    )
    return list(
        {
            ref.id: ref.model_copy(update={"revision": None})
            for ref in [*defaults, *task.documents]
        }.values()
    )


def latest_documents(
    store: Workspace, db: sqlite3.Connection, task: Task
) -> list[DocumentSnapshot]:
    # Selections are live; only publications, runs and review/result evidence pin revisions.
    return select(
        db,
        task.workspace_id,
        [],
        selected_references(store, db, task),
    )


def result_documents(db: sqlite3.Connection, result: "ResultVersion") -> list[DocumentSnapshot]:
    if result.guidance_documents is not None:
        return result.guidance_documents
    row = db.execute(
        "SELECT json_extract(data,'$.documents') FROM runs WHERE id=?", (result.run_id,)
    ).fetchone()
    return [DocumentSnapshot.model_validate(doc) for doc in json.loads(row[0] or "[]")]


def pending(
    store: Workspace,
    db: sqlite3.Connection,
    task: TaskRevision | GuidanceInputs,
    result: "ResultVersion | None" = None,
) -> bool:
    if task.archived or task.status == "done" or not task.publication:
        return False
    try:
        references = selected_references(store, db, task)
        headers = dict(
            db.execute(
                "SELECT id,revision FROM workspace_documents WHERE workspace_id=? "
                "AND deleted_at IS NULL AND id IN (SELECT value FROM json_each(?))",
                (task.workspace_id, json.dumps([ref.id for ref in references])),
            )
        )
        desired = sorted((ref.id, headers.get(ref.id), ref.purpose) for ref in references)
    except ApplicationError:
        return True  # Missing selections are a scheduling blocker, never a read-side 500.

    def identity(documents: list[DocumentSnapshot]) -> list[tuple[str, int, str]]:
        return sorted((doc.id, doc.revision, doc.purpose) for doc in documents)

    return identity(task.publication.documents) != desired or (
        result is not None and identity(result_documents(db, result)) != desired
    )


def require_current(
    store: Workspace, db: sqlite3.Connection, task: Task, result: "ResultVersion | None" = None
) -> None:
    if pending(store, db, task, result):
        raise ApplicationError("guidance_changed", "Updated guidance is awaiting review.", 409)


def binding(
    store: Workspace, db: sqlite3.Connection, task: Task, result: "ResultVersion | None"
) -> GuidanceBinding:
    reviewer = Reviewers.resolved(db, task.workspace_id, task.id).effective
    if not reviewer.agent:
        raise ApplicationError("reviewer_required", "Choose a reviewer to reconcile guidance.", 409)
    assert task.publication
    from flowfield.execution_models import Run
    from flowfield.subtasks import Subtasks

    row = db.execute(
        "SELECT data FROM runs WHERE workspace_id=? AND task_id=? ORDER BY number DESC LIMIT 1",
        (task.workspace_id, task.id),
    ).fetchone()
    run = Run.model_validate_json(row[0]) if row else None
    candidate = (result.candidate_commit or result.source_commit) if result else None
    if (
        not candidate
        and run
        and run.completion == "code"
        and run.status in ("waiting_for_input", "changes_requested", "stopped", "failed")
    ):
        candidate = run.checkpoint_commit or run.input_checkpoint or run.result_commit
    if not candidate and task.project_id and task.publication.completion == "code":
        from flowfield.adapters import git_integration

        candidate = git_integration.target(
            Path(store._project(db, task.project_id).path), task.publication.target_branch or "HEAD"
        )
    return GuidanceBinding(
        task_revision=task.revision,
        agreement_revision=task.agreement_revision,
        documents=latest_documents(store, db, task),
        previous_documents=result_documents(db, result) if result else task.publication.documents,
        reviewer=reviewer,
        result_revision=result.revision if result else None,
        run_id=run.id if run else None,
        run_revision=run.revision if run else None,
        candidate_commit=candidate,
        task_context=json.dumps(
            {
                "task": TaskRevision.model_validate(task.model_dump()).model_dump(),
                "subtasks": [
                    child.model_dump()
                    for child in Subtasks.read_all(db, task.workspace_id, task.id)
                ],
            }
        ),
        result_context=result.model_dump_json(
            exclude={"next_action", "reuse_action", "independent_review", "files"}
        )
        if result
        else run.model_dump_json()
        if run
        else "",
    )


def prepare(owner: "TaskReviews", workspace: str, task_id: str) -> "TaskReview | None":
    from flowfield.results import Results
    from flowfield.task_reviews import TaskReview

    store = owner.store
    with store.connection(write=True, workspace_id=workspace) as db:
        task = store._task(db, workspace, task_id)
        if task.archived or task.status in ("done", "backlog") or not task.publication:
            return None
        if db.execute(
            "SELECT 1 FROM runs WHERE workspace_id=? AND task_id=? AND status IN "
            "('preparing','running','stopping','uncertain')",
            (workspace, task.id),
        ).fetchone():
            return None
        row = db.execute(
            "SELECT id FROM result_versions WHERE workspace_id=? AND task_id=? "
            "ORDER BY version DESC LIMIT 1",
            (workspace, task.id),
        ).fetchone()
        result = Results(store)._get(db, workspace, row[0]) if row else None
        if result and result.status == "delivering" and result.integration_id:
            integration = db.execute(
                "SELECT status FROM integrations WHERE id=?", (result.integration_id,)
            ).fetchone()
            if integration and integration[0] in ("applying", "integrated"):
                return None  # Resolve an already reserved Git delivery before changing its result.
        if result and (
            task.status == "up_next"
            or result.status in ("changes_requested", "delivered", "cancelled")
            or result.run_id
            != db.execute(
                "SELECT id FROM runs WHERE workspace_id=? AND task_id=? "
                "ORDER BY number DESC LIMIT 1",
                (workspace, task.id),
            ).fetchone()[0]
        ):
            result = None
        if result and result.completion == "code" and result.status == "preparing":
            # Candidate validation changes the result binding. Let it settle before
            # spending a Reviewer turn that would be discarded as stale on completion.
            # Reports must still reconcile here: they can finish during preparation.
            return None
        if not pending(store, db, task, result):
            return None
        stopped = db.execute(
            "SELECT data FROM task_reviews WHERE workspace_id=? AND task_id=? "
            "AND json_extract(data,'$.guidance') IS NOT NULL ORDER BY number DESC LIMIT 1",
            (workspace, task.id),
        ).fetchone()
        if stopped:
            previous_review = TaskReview.model_validate_json(stopped[0])
            if previous_review.status in ("stopped", "failed") and owner.is_current(
                db, previous_review
            ):
                return previous_review
        # Do not change inputs underneath another native owner. Its freshness check will
        # discard the old conclusion, then the next scheduler pass coalesces current edits.
        if db.execute(
            "SELECT 1 FROM task_reviews WHERE workspace_id=? AND task_id=? AND status IN "
            "('preparing','running','uncertain')",
            (workspace, task.id),
        ).fetchone():
            return None
        if task.status == "in_review":
            from flowfield.execution import Execution

            Execution(store)._task_state(db, task, "in_progress", "Reviewing updated guidance.")
            task = store._task(db, workspace, task.id)
        frozen = binding(store, db, task, result)
        digest = hashlib.sha256(frozen.model_dump_json().encode()).hexdigest()
        previous = db.execute(
            "SELECT data FROM task_reviews WHERE workspace_id=? AND task_id=? "
            "AND binding=? AND json_extract(data,'$.guidance') IS NOT NULL",
            (workspace, task.id, digest),
        ).fetchone()
        if previous:
            return TaskReview.model_validate_json(previous[0])
        for row in db.execute(
            "SELECT data FROM task_reviews WHERE workspace_id=? AND task_id=? "
            "AND (status IN ('queued','waiting') OR (status='decided' AND result_id=? "
            "AND json_extract(data,'$.guidance') IS NULL))",
            (workspace, task.id, result.id if result else None),
        ).fetchall():
            old = TaskReview.model_validate_json(row[0])
            old.status, old.problem, old.completed_at = "stale", "Guidance changed.", now()
            owner._retire_question(
                db, old, "Guidance changed; a new review will use the current context."
            )
            owner._save(db, old)
        review = TaskReview(
            id=uuid4().hex,
            workspace_id=workspace,
            task_id=task.id,
            result_id=result.id if result else None,
            guidance=frozen,
            created_at=now(),
        )
        db.execute(
            "INSERT INTO task_reviews(id,workspace_id,task_id,result_id,binding,status,data) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                review.id,
                workspace,
                task.id,
                review.result_id,
                digest,
                review.status,
                review.model_dump_json(),
            ),
        )
        return review


def freshness_problem(
    owner: "TaskReviews", db: sqlite3.Connection, review: "TaskReview"
) -> str | None:
    from flowfield.results import Results

    if review.applied_at:
        return None  # Historical reconciliation, not reusable delivery authority.
    task = owner.store._task(db, review.workspace_id, review.task_id)
    if task.archived or task.status in ("done", "backlog"):
        return "Task is closed."
    result = (
        Results(owner.store)._get(db, review.workspace_id, review.result_id)
        if review.result_id
        else None
    )
    if result:
        Results(owner.store)._current(db, result)
    if db.execute(
        "SELECT 1 FROM runs WHERE workspace_id=? AND task_id=? "
        "AND status IN ('preparing','running','stopping','uncertain')",
        (review.workspace_id, review.task_id),
    ).fetchone():
        return "Wait for the active worker before reconciling guidance."
    if binding(owner.store, db, task, result) != review.guidance:
        return "Guidance or task inputs changed; a fresh reconciliation will follow."
    return None


def apply(
    owner: "TaskReviews", db: sqlite3.Connection, review: "TaskReview", decision: GuidanceDecision
) -> None:
    from flowfield.execution import Execution
    from flowfield.results import Results

    assert review.guidance
    store = owner.store
    task = store._task(db, review.workspace_id, review.task_id)
    assert task.publication
    plan_changed = apply_plan(db, task, decision)
    changes = {
        key: value
        for key, value in decision.model_dump().items()
        if key in ("body", "acceptance_criteria", "dependencies") and value is not None
    }
    if "dependencies" in changes:
        changes["dependencies"] = store._dependency_ids(
            db, task.workspace_id, changes["dependencies"]
        )
    dependencies_changed = changes.get("dependencies", task.dependencies) != task.dependencies
    revised = TaskRevision.model_validate(
        {
            **task.model_dump(),
            **changes,
            "revision": task.revision + 1,
            "agreement_revision": task.agreement_revision + int(plan_changed),
            "updated_at": now(),
            "updated_by": "reviewer",
            "change_note": decision.summary,
            "publication": task.publication.model_copy(
                update={
                    "documents": review.guidance.documents,
                    "created_at": now(),
                    "author": "reviewer",
                }
            ),
        }
    )
    # Existing validation owns dependency cycles and downstream invalidation.
    store._apply_task(db, revised, reconciled=True)
    revised = TaskRevision.model_validate(store._task(db, task.workspace_id, task.id).model_dump())
    if review.result_id:
        results = Results(store)
        result = results._get(db, task.workspace_id, review.result_id)
        run = results.execution._run(db, task.workspace_id, result.run_id)
        if plan_changed or dependencies_changed or decision.rework:
            result.status = run.status = "changes_requested"
            result.feedback = run.feedback = decision.request or decision.summary
            if run.purpose == "assembly":
                from flowfield.subtasks import Subtasks

                Subtasks.request_changes(db, revised, None, result.feedback)
            results.execution._save(db, run)
            Execution(store)._task_state(
                db,
                store._task(db, task.workspace_id, task.id),
                "up_next",
                "Guidance reconciled; revising the implementation.",
            )
            results._save(db, result)
        else:
            # Preserve the old result, its guidance and any approval as history. A new
            # version reuses the source, validates it and gets its own assessment/approval.
            result.status, result.problem_code = "stale", "guidance_changed"
            result.problem = "Superseded by guidance reconciliation."
            results._save(db, result)
            run.status = "in_review"
            results.execution._save(db, run)
            results.capture(
                db,
                run,
                output_ids=result.output_ids,
                reconciled_task_revision=revised.revision,
                guidance_documents=review.guidance.documents,
            )
    review.applied_at = now()
    review.applied_agreement_revision = revised.agreement_revision


def apply_plan(db: sqlite3.Connection, task: Task, decision: GuidanceDecision) -> bool:
    """Revise existing local instructions through the same graph and history owners."""
    from flowfield.subtasks import Subtasks

    records = {child.id: child for child in Subtasks.read_all(db, task.workspace_id, task.id)}
    changed = {}
    for edit in decision.subtasks:
        child = Subtasks.read(db, task.workspace_id, task.id, edit.id)
        if child.id in changed:
            raise ApplicationError("duplicate_subtask", "Propose each subtask change once.")
        updates = edit.model_dump(exclude={"id"}, exclude_none=True)
        if edit.dependencies is not None:
            updates["dependencies"] = sorted(
                {Subtasks.read(db, task.workspace_id, task.id, key).id for key in edit.dependencies}
            )
        value = child.model_copy(update=updates)
        if value != child:
            changed[child.id] = value
    if not changed:
        return False
    Subtasks._graph(list((records | changed).values()))
    # Changed guidance already invalidates the assembly signature. Keep existing code
    # as continuation evidence and record why the contributions need another pass.
    Subtasks.request_changes(db, task, None, decision.request or decision.summary)
    for identity, value in changed.items():
        current = Subtasks.read(db, task.workspace_id, task.id, identity)
        Subtasks.save(
            db,
            current.model_copy(
                update={
                    "body": value.body,
                    "dependencies": value.dependencies,
                    "revision": current.revision + 1,
                    "updated_at": now(),
                    "updated_by": "reviewer",
                }
            ),
        )
    return True


def reconciled_run(db: sqlite3.Connection, task: Task, run_id: str) -> bool:
    """A service-applied reconciliation can update a checkpoint continuation's agreement."""
    return (
        db.execute(
            "SELECT 1 FROM task_reviews WHERE workspace_id=? AND task_id=? AND status='decided' "
            "AND json_extract(data,'$.guidance.run_id')=? "
            "AND json_extract(data,'$.applied_agreement_revision')=? "
            "AND json_extract(data,'$.applied_at') IS NOT NULL",
            (task.workspace_id, task.id, run_id, task.agreement_revision),
        ).fetchone()
        is not None
    )


def context(owner: "TaskReviews", review: "TaskReview", section: str) -> str:
    assert review.guidance
    with owner.store.connection() as db:
        if section == "task":
            return review.guidance.task_context
        if section == "previous_guidance":
            return sections(db, review.guidance.previous_documents)["documentation"]
        if section == "current_guidance":
            return sections(db, review.guidance.documents)["documentation"]
        if section.startswith("document:"):
            selected = sections(db, review.guidance.documents)
            if section in selected:
                return selected[section]
        if section.startswith("previous:document:"):
            selected = sections(db, review.guidance.previous_documents)
            if section.removeprefix("previous:") in selected:
                return selected[section.removeprefix("previous:")]
        if section == "retained_work":
            return review.guidance.result_context or "Implementation has not produced a result."
        if section == "earlier_answers":
            return json.dumps(
                [
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT q.data FROM questions q JOIN task_reviews r "
                        "ON r.id=json_extract(q.data,'$.origin_review_id') "
                        "WHERE q.workspace_id=? AND q.task_id=? "
                        "AND q.status IN ('withdrawn','applied') "
                        "AND json_extract(q.data,'$.answer') IS NOT NULL "
                        "AND json_extract(r.data,'$.guidance') IS NOT NULL "
                        "ORDER BY q.number DESC LIMIT 10",
                        (review.workspace_id, review.task_id),
                    )
                ]
            )
    raise ApplicationError("not_found", "Guidance context section not found.", 404)
