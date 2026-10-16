"""Browser projections: graph metadata for cards, explicit detail and revision reads."""

import sqlite3
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from flowfield.application import (
    Milestone,
    Project,
    TaskCreate,
    TaskEdit,
    TaskPriority,
    TaskReference,
    TaskRevision,
    Workspace,
)
from flowfield.attention import (
    AttentionColumn,
    AttentionPage,
    attention_page,
    workspace_attention_counts,
    workspace_attention_requests,
)
from flowfield.document_models import DocumentUpdate
from flowfield.errors import ApplicationError
from flowfield.labels import Labels, WorkspaceLabel
from flowfield.milestone_models import MilestoneUpdate
from flowfield.milestone_verification import board_verifications
from flowfield.milestones import Milestones, milestone_update
from flowfield.publication import (
    Publication,
    PublicationStatus,
    Readiness,
    preparation_issue,
    publication_status,
    readiness,
    stages_need_reconciliation,
    verification_issue,
)
from flowfield.questions import QuestionReference, Questions, blocking_questions
from flowfield.reads import task_metadata
from flowfield.verification_models import MilestoneVerificationSummary
from flowfield.work_state import WorkState, task_state
from flowfield.workspaces import WorkspaceRecord, Workspaces


class TaskCard(TaskReference):
    document_updates: list[DocumentUpdate] = Field(default_factory=list)
    milestone_update: MilestoneUpdate | None = None
    preparation_issue: str | None
    state: WorkState | None = None
    project_id: str
    labels: list[str]
    milestone_id: str | None
    revision: int
    created_at: str
    updated_at: str
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
    archive_blocker: str | None = None
    prerequisites: list[TaskReference]
    dependents: list[TaskReference]


class BrowserBoard(BaseModel):
    labels: list[WorkspaceLabel]
    project: Project
    milestone_verifications: dict[str, MilestoneVerificationSummary] = Field(default_factory=dict)
    milestones: list[Milestone]
    tasks: list[TaskCard]
    needs_you_count: int
    awaiting_application_count: int
    pending_code: dict[str, list[TaskReference]]


class BrowserWorkspaceBoard(BaseModel):
    document_count: int
    workspace: WorkspaceRecord
    projects: list[Project]
    project_problems: dict[str, str]
    labels: list[WorkspaceLabel]
    milestone_verifications: dict[str, MilestoneVerificationSummary] = Field(default_factory=dict)
    milestone_delivery_ids: list[str] = Field(default_factory=list)
    milestones: list[Milestone]
    tasks: list[TaskCard]
    needs_you_count: int
    attention_work_keys: list[str] = Field(default_factory=list)
    attention_requests: dict[str, str] = Field(default_factory=dict)
    awaiting_application_count: int
    pending_code: dict[str, list[TaskReference]]


def task_timeline(
    db: sqlite3.Connection, project_id: str, task_id: str | None = None
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
            "FROM task_revisions WHERE project_id=?"
            + (" AND task_id=?" if task_id is not None else "")
            + "), "
            "times AS (SELECT task_id, "
            "max(CASE WHEN status IS NOT previous_status THEN updated_at END) AS entered, "
            "max(CASE WHEN archived=1 AND coalesce(previous_archived,0)=0 "
            "THEN revision END) AS archive_revision FROM steps GROUP BY task_id) "
            "SELECT times.task_id, entered, json_extract(r.data, '$.updated_at') AS archived_at, "
            "json_extract(r.data, '$.updated_by') AS archived_by FROM times "
            "LEFT JOIN task_revisions r ON r.project_id=? AND r.task_id=times.task_id "
            "AND r.revision=times.archive_revision",
            (project_id, task_id, project_id) if task_id is not None else (project_id, project_id),
        )
    }


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
    ) -> TaskCard:
        blocked = [records[key] for key in task.dependencies if key not in completed]
        questions = blocking_questions(db, task.project_id, task.id)
        publication = publication_status(task)
        state = task_state(self.workspace, db, task.project_id, task.id)
        from flowfield.document_updates import task_updates

        return TaskCard(
            **task.model_dump(exclude={"body", "change_note"}),
            position=position,
            document_updates=task_updates(db, task.project_id, task.id)
            if task.publication and task.publication.documents
            else [],
            milestone_update=milestone_update(db, task)
            if self.workspace.schema_version >= 68
            else None,
            status_changed_at=timeline["entered"],
            archived_at=timeline["archived_at"] if task.archived else None,
            archived_by=timeline["archived_by"] if task.archived else None,
            publication_status=publication,
            preparation_issue=preparation_issue(db, task, include_draft_hint=False),
            readiness=readiness(
                task,
                publication,
                blocked=bool(blocked or questions or verification_issue(db, task)),
                stages_stale=stages_need_reconciliation(
                    db, task.project_id, task.id, task.agreement_revision
                ),
            ),
            blocked_by=[TaskReference(**item.model_dump()) for item in blocked],
            blocking_questions=questions,
            state=state,
        )

    def _project_cards(
        self,
        db: sqlite3.Connection,
        project_id: str,
        records: dict[str, TaskRevision],
        positions: dict[str, int],
        completed: set[str],
    ) -> tuple[list[TaskCard], dict[str, list[TaskReference]]]:
        timeline = task_timeline(db, project_id)
        available = {
            row[0]
            for row in db.execute(
                "SELECT task_id FROM work_runs r WHERE project_id=? AND status='accepted' "
                "AND (json_extract(data,'$.code_available')=1 "
                "OR json_extract(data,'$.completion')='report') AND NOT EXISTS "
                "(SELECT 1 FROM work_runs newer WHERE newer.project_id=r.project_id "
                "AND newer.task_id=r.task_id AND newer.status='accepted' "
                "AND newer.number>r.number)",
                (project_id,),
            )
        }
        return (
            [
                self._card(
                    db,
                    item,
                    records,
                    completed,
                    positions[self.workspace._record_key(item)],
                    timeline[item.id],
                )
                for item in records.values()
                if item.project_id == project_id
            ],
            {
                task.id: [
                    TaskReference(**records[key].model_dump())
                    for key in task.dependencies
                    if key in completed
                    and records[key].project_id == project_id
                    and records[key].id not in available
                ]
                for task in records.values()
                if task.project_id == project_id and task.status in ("backlog", "up_next")
            },
        )

    def board(self, project_id: str) -> BrowserBoard:
        with self.workspace.connection() as db:
            project = self.workspace._project(db, project_id)
            records, positions = task_metadata(self.workspace, db, project_id)
            cards, pending = self._project_cards(
                db, project_id, records, positions, self.workspace._completed(records, db)
            )
            counts = Questions(self.workspace).counts(db, project_id)
            return BrowserBoard(
                labels=Labels.read_all(db, self.workspace._project_workspace(db, project_id)),
                project=project,
                milestones=self.workspace._milestones(db, project_id),
                tasks=cards,
                milestone_verifications=board_verifications(
                    db, self.workspace._project_workspace(db, project_id)
                ),
                needs_you_count=counts["needs_you_count"],
                awaiting_application_count=counts["awaiting_application_count"],
                pending_code=pending,
            )

    def workspace_board(self, workspace_id: str) -> BrowserWorkspaceBoard:
        with self.workspace.connection() as db:
            owner = Workspaces.read(db, workspace_id)
            projects = [
                self.workspace._project(db, row[0])
                for row in db.execute(
                    "SELECT project_id FROM workspace_projects "
                    "WHERE workspace_id=? ORDER BY project_id",
                    (workspace_id,),
                )
            ]
            records, positions = (
                task_metadata(self.workspace, db, projects[0].id) if projects else ({}, {})
            )
            completed = self.workspace._completed(records, db)
            cards: list[TaskCard] = []
            pending: dict[str, list[TaskReference]] = {}
            for project in projects:
                items, blocked = self._project_cards(db, project.id, records, positions, completed)
                cards.extend(items)
                pending.update({item.key: blocked.get(item.id, []) for item in items})
            counts = workspace_attention_counts(db, workspace_id)
            attention_requests = workspace_attention_requests(db, workspace_id)
            return BrowserWorkspaceBoard(
                document_count=db.execute(
                    "SELECT count(*) FROM workspace_documents WHERE workspace_id=? "
                    "AND deleted_at IS NULL",
                    (workspace_id,),
                ).fetchone()[0],
                workspace=owner,
                projects=projects,
                project_problems=dict(
                    db.execute(
                        "SELECT p.project_id,p.problem FROM project_worker_problems p "
                        "JOIN workspace_projects w USING(project_id) WHERE w.workspace_id=?",
                        (workspace_id,),
                    ).fetchall()
                ),
                labels=Labels.read_all(db, workspace_id),
                milestones=Milestones.read_all(db, workspace_id),
                milestone_verifications=board_verifications(db, workspace_id),
                milestone_delivery_ids=[
                    row[0]
                    for row in db.execute(
                        "SELECT milestone_id FROM milestone_deliveries WHERE workspace_id=?",
                        (workspace_id,),
                    )
                ],
                tasks=cards,
                pending_code=pending,
                needs_you_count=counts.get("action", 0),
                attention_work_keys=sorted(attention_requests),
                attention_requests=attention_requests,
                awaiting_application_count=counts.get("waiting", 0),
            )

    def task(self, project_id: str, task_id: str) -> TaskDetail:
        with self.workspace.connection() as db:
            identity = self.workspace._activity_scope(db, project_id, task_id)
            records, positions = task_metadata(self.workspace, db, project_id)
            current = TaskRevision.model_validate_json(
                db.execute(
                    "SELECT data FROM tasks WHERE project_id=? AND id=?", (project_id, identity)
                ).fetchone()[0]
            )
            card = self._card(
                db,
                current,
                records,
                self.workspace._completed(records, db),
                positions[self.workspace._record_key(current)],
                task_timeline(db, project_id, current.id)[current.id],
            )
            archive_issue = self.workspace.archive_issue(db, current)
            return TaskDetail(
                **card.model_dump(),
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

    def revision(self, project_id: str, task_id: str, revision: int) -> TaskRevision:
        with self.workspace.connection() as db:
            identity = self.workspace._activity_scope(db, project_id, task_id)
            row = db.execute(
                "SELECT data FROM task_revisions WHERE project_id=? AND task_id=? AND revision=?",
                (project_id, identity, revision),
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Task revision not found.", 404)
            return TaskRevision.model_validate_json(row[0])


def browser_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/view")
    Service = Annotated[Workspace, Depends(workspace)]

    @router.get("/board")
    def board(project_id: str, service: Service) -> BrowserBoard:
        return BrowserReads(service).board(project_id)

    @router.get("/tasks/{task_id}")
    def task(project_id: str, task_id: str, service: Service) -> TaskDetail:
        return BrowserReads(service).task(project_id, task_id)

    @router.post("/tasks", status_code=201)
    def create_task(project_id: str, request: TaskCreate, service: Service) -> TaskDetail:
        record = service.create_task(project_id, request)
        return BrowserReads(service).task(project_id, record.id)

    @router.put("/tasks/{task_id}")
    def edit_task(project_id: str, task_id: str, request: TaskEdit, service: Service) -> TaskDetail:
        record = service.edit_task(project_id, task_id, request)
        return BrowserReads(service).task(project_id, record.id)

    @router.post("/tasks/{task_id}/prioritize")
    def prioritize_task(
        project_id: str, task_id: str, request: TaskPriority, service: Service
    ) -> TaskDetail:
        record = service.prioritize_task(project_id, task_id, request)
        return BrowserReads(service).task(project_id, record.id)

    @router.get("/tasks/{task_id}/revisions/{revision}")
    def revision(project_id: str, task_id: str, revision: int, service: Service) -> TaskRevision:
        return BrowserReads(service).revision(project_id, task_id, revision)

    @router.get("/attention")
    def attention(
        project_id: str,
        column: AttentionColumn,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> AttentionPage:
        return attention_page(workspace(), project_id, column, offset, limit)

    return router
