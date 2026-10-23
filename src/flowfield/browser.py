"""Browser projections: graph metadata for cards, explicit detail and revision reads."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from flowfield.application import (
    Project,
    TaskCreate,
    TaskEdit,
    TaskPriority,
    TaskReference,
    TaskRevision,
    Workspace,
)
from flowfield.attention import (
    workspace_attention_counts,
    workspace_attention_requests,
)
from flowfield.document_models import DocumentUpdate
from flowfield.document_updates import project_updates
from flowfield.errors import ApplicationError
from flowfield.labels import Labels, WorkspaceLabel
from flowfield.prerequisite_reads import PendingCode, pending_code
from flowfield.publication import (
    Publication,
    PublicationStatus,
    Readiness,
    preparation_issue,
    publication_status,
    readiness,
)
from flowfield.questions import (
    QuestionReference,
    blocking_questions,
    project_blocking_questions,
)
from flowfield.reads import task_metadata
from flowfield.subtasks import Subtask, Subtasks
from flowfield.task_read_facts import TaskReadFacts, read_task_facts
from flowfield.work_state import WorkState, task_state
from flowfield.workspaces import WorkspaceRecord, Workspaces


class TaskCard(TaskReference):
    document_updates: list[DocumentUpdate] = Field(default_factory=list)
    preparation_issue: str | None
    state: WorkState | None = None
    workspace_id: str
    labels: list[str]
    revision: int
    created_at: str
    updated_at: str
    activity_at: str
    updated_by: str
    position: int
    status_changed_at: str
    archived_at: str | None
    archived_by: str | None
    dependencies: list[str]
    readiness: Readiness
    publication_status: PublicationStatus
    agreement_revision: int
    publication: Publication | None
    blocked_by: list[TaskReference]
    blocking_questions: list[QuestionReference]


class TaskDetail(TaskRevision, TaskCard):
    subtasks: list[Subtask] = Field(default_factory=list)
    archive_blocker: str | None = None
    prerequisites: list[TaskReference]
    dependents: list[TaskReference]


class BrowserWorkspaceBoard(BaseModel):
    document_count: int
    workspace: WorkspaceRecord
    projects: list[Project]
    labels: list[WorkspaceLabel]
    tasks: list[TaskCard]
    needs_you_count: int
    attention_work_keys: list[str] = Field(default_factory=list)
    attention_requests: dict[str, str] = Field(default_factory=dict)
    awaiting_application_count: int
    pending_code: dict[str, list[PendingCode]]


def task_timeline(
    db: sqlite3.Connection, workspace_id: str, task_id: str | None = None
) -> dict[str, sqlite3.Row]:
    # SQLite projects just state transitions; Python never loads historical bodies.
    return {
        row["task_id"]: row
        for row in db.execute(
            "WITH steps AS (SELECT task_id, revision, "
            "json_extract(data, '$.status') AS status, "
            "json_extract(data, '$.archived') AS archived, "
            "json_extract(data, '$.updated_at') AS updated_at, "
            "lag(json_extract(data, '$.status')) OVER "
            "(PARTITION BY task_id ORDER BY revision) AS previous_status, "
            "lag(json_extract(data, '$.archived')) OVER "
            "(PARTITION BY task_id ORDER BY revision) AS previous_archived "
            "FROM task_revisions WHERE workspace_id=?"
            + (" AND task_id=?" if task_id is not None else "")
            + "), "
            "times AS (SELECT task_id, "
            "max(CASE WHEN status IS NOT previous_status THEN updated_at END) AS entered, "
            "max(CASE WHEN archived=1 AND coalesce(previous_archived,0)=0 "
            "THEN revision END) AS archive_revision FROM steps GROUP BY task_id), "
            "events AS (SELECT task_id,created_at timestamp FROM activity WHERE workspace_id=? "
            "UNION ALL SELECT id,json_extract(data,'$.updated_at') FROM tasks WHERE workspace_id=? "
            "UNION ALL SELECT task_id,json_extract(data,'$.updated_at') FROM questions WHERE"
            " workspace_id=? "
            "UNION ALL SELECT task_id,json_extract(data,'$.updated_at') FROM agent_permissions"
            " WHERE workspace_id=? "
            "UNION ALL SELECT"
            " task_id,coalesce(json_extract(data,'$.ended_at'),json_extract(data,'$.started_at'),"
            "json_extract(data,'$.created_at')) FROM runs WHERE workspace_id=? "
            "UNION ALL SELECT"
            " task_id,coalesce(json_extract(data,'$.completed_at'),json_extract(data,"
            "'$.created_at')) FROM task_reviews WHERE workspace_id=? "
            "UNION ALL SELECT task_id,json_extract(data,'$.created_at') FROM task_replies WHERE"
            " workspace_id=?), "
            "recent AS (SELECT task_id,max(timestamp) activity_at FROM events GROUP BY task_id) "
            "SELECT times.task_id, entered, recent.activity_at, json_extract(r.data,"
            " '$.updated_at') AS archived_at, "
            "json_extract(r.data, '$.updated_by') AS archived_by FROM times "
            "JOIN recent ON recent.task_id=times.task_id LEFT JOIN task_revisions r ON"
            " r.workspace_id=? AND r.task_id=times.task_id "
            "AND r.revision=times.archive_revision",
            ((workspace_id, task_id) if task_id is not None else (workspace_id,))
            + (workspace_id,) * 8,
        )
    }


@dataclass(frozen=True)
class CardReadBatch:
    facts: dict[str, TaskReadFacts]
    questions: dict[str, list[QuestionReference]]
    documents: dict[str, list[DocumentUpdate]]


class BrowserReads:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _card(
        self,
        db: sqlite3.Connection,
        task: TaskRevision,
        records: dict[str, TaskRevision],
        completed: set[str],
        position: int,
        timeline: sqlite3.Row,
        batch: CardReadBatch | None = None,
    ) -> TaskCard:
        blocked = (
            [records[key] for key in task.dependencies if key not in completed]
            if task.status != "done"
            else []
        )
        questions = (
            batch.questions.get(task.id, [])
            if batch
            else blocking_questions(db, task.workspace_id, task.id)
        )
        publication = publication_status(task)
        facts = (
            batch.facts[task.id]
            if batch
            else read_task_facts(self.workspace, db, task.workspace_id, task.id)[task.id]
        )
        state = task_state(self.workspace, db, task.workspace_id, task.id, facts=facts)
        from flowfield.document_updates import task_updates

        return TaskCard(
            **task.model_dump(exclude={"body", "change_note"}),
            position=position,
            document_updates=(
                batch.documents.get(task.id, [])
                if batch
                else task_updates(db, task.workspace_id, task.id)
            )
            if task.publication and task.publication.documents
            else [],
            activity_at=timeline["activity_at"],
            status_changed_at=timeline["entered"],
            archived_at=timeline["archived_at"] if task.archived else None,
            archived_by=timeline["archived_by"] if task.archived else None,
            publication_status=publication,
            preparation_issue=preparation_issue(db, task, include_draft_hint=False, facts=facts),
            readiness=readiness(
                task,
                publication,
                blocked=bool(blocked or questions),
            ),
            blocked_by=[TaskReference(**item.model_dump()) for item in blocked],
            blocking_questions=questions,
            state=state,
        )

    def _project_cards(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        records: dict[str, TaskRevision],
        positions: dict[str, int],
        completed: set[str],
    ) -> tuple[list[TaskCard], dict[str, list[PendingCode]]]:
        timeline = task_timeline(db, workspace_id)
        batch = CardReadBatch(
            facts=read_task_facts(self.workspace, db, workspace_id),
            questions=project_blocking_questions(db, workspace_id),
            documents=(project_updates(db, workspace_id)),
        )
        return (
            [
                self._card(
                    db,
                    item,
                    records,
                    completed,
                    positions[self.workspace._record_key(item)],
                    timeline[item.id],
                    batch,
                )
                for item in records.values()
                if item.workspace_id == workspace_id
            ],
            pending_code(self.workspace, db, workspace_id, records, completed),
        )

    def workspace_board(self, workspace_id: str) -> BrowserWorkspaceBoard:
        with self.workspace.connection() as db:
            owner = Workspaces.read(db, workspace_id)
            projects = [
                self.workspace._project(db, row[0])
                for row in db.execute(
                    "SELECT project_id FROM workspace_projects WHERE workspace_id=? "
                    "ORDER BY project_id",
                    (workspace_id,),
                )
            ]
            records, positions = task_metadata(self.workspace, db, workspace_id)
            completed = self.workspace._completed(records, db)
            cards, pending = self._project_cards(db, workspace_id, records, positions, completed)
            counts = workspace_attention_counts(db, workspace_id)
            requests = workspace_attention_requests(db, workspace_id)
            return BrowserWorkspaceBoard(
                workspace=owner,
                projects=projects,
                labels=Labels.read_all(db, workspace_id),
                document_count=db.execute(
                    "SELECT count(*) FROM workspace_documents WHERE workspace_id=? AND "
                    "deleted_at IS NULL",
                    (workspace_id,),
                ).fetchone()[0],
                tasks=cards,
                needs_you_count=counts.get("action", 0),
                awaiting_application_count=counts.get("waiting", 0),
                pending_code=pending,
                attention_work_keys=sorted(requests),
                attention_requests=requests,
            )

    def task(self, workspace_id: str, task_id: str) -> TaskDetail:
        with self.workspace.connection() as db:
            identity = self.workspace._activity_scope(db, workspace_id, task_id)
            records, positions = task_metadata(self.workspace, db, workspace_id)
            current = TaskRevision.model_validate_json(
                db.execute(
                    "SELECT data FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, identity)
                ).fetchone()[0]
            )
            card = self._card(
                db,
                current,
                records,
                self.workspace._completed(records, db),
                positions[self.workspace._record_key(current)],
                task_timeline(db, workspace_id, current.id)[current.id],
            )
            archive_issue = self.workspace.archive_issue(db, current)
            return TaskDetail(
                **card.model_dump(),
                subtasks=Subtasks.read_all(db, workspace_id, current.id),
                archive_blocker=archive_issue.message if archive_issue else None,
                body=current.body,
                change_note=current.change_note,
                prerequisites=[
                    TaskReference(**records[key].model_dump()) for key in current.dependencies
                ],
                dependents=[
                    TaskReference(**item.model_dump())
                    for item in records.values()
                    if self.workspace._record_key(current) in item.dependencies
                ],
            )

    def revision(self, workspace_id: str, task_id: str, revision: int) -> TaskRevision:
        with self.workspace.connection() as db:
            identity = self.workspace._activity_scope(db, workspace_id, task_id)
            row = db.execute(
                "SELECT data FROM task_revisions WHERE workspace_id=? AND task_id=? AND revision=?",
                (workspace_id, identity, revision),
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Task revision not found.", 404)
            return TaskRevision.model_validate_json(row[0])


def browser_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/view")
    Service = Annotated[Workspace, Depends(workspace)]

    @router.get("/tasks/{task_id}")
    def task(workspace_id: str, task_id: str, service: Service) -> TaskDetail:
        return BrowserReads(service).task(workspace_id, task_id)

    @router.post("/tasks", status_code=201)
    def create_task(workspace_id: str, request: TaskCreate, service: Service) -> TaskDetail:
        record = service.create_task(workspace_id, request)
        return BrowserReads(service).task(workspace_id, record.id)

    @router.put("/tasks/{task_id}")
    def edit_task(
        workspace_id: str, task_id: str, request: TaskEdit, service: Service
    ) -> TaskDetail:
        record = service.edit_task(workspace_id, task_id, request)
        return BrowserReads(service).task(workspace_id, record.id)

    @router.post("/tasks/{task_id}/prioritize")
    def prioritize_task(
        workspace_id: str, task_id: str, request: TaskPriority, service: Service
    ) -> TaskDetail:
        record = service.prioritize_task(workspace_id, task_id, request)
        return BrowserReads(service).task(workspace_id, record.id)

    @router.get("/tasks/{task_id}/revisions/{revision}")
    def revision(workspace_id: str, task_id: str, revision: int, service: Service) -> TaskRevision:
        return BrowserReads(service).revision(workspace_id, task_id, revision)

    return router
