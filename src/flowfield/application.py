"""Service-owned project board operations with guarded storage maintenance."""

import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

from flowfield import __version__
from flowfield.activity import (
    ACTIVITY_SELECT,
    ActivityCreate,
    ActivityEntry,
    ActivityPage,
    EntryKind,
)
from flowfield.document_models import (
    DocumentReference,
    DocumentReferences,
    DocumentSnapshot,
    DocumentUpdate,
)
from flowfield.errors import ApplicationError
from flowfield.execution_models import ACTIVE
from flowfield.project_config import Identifier, ProjectConfig, Title, read_config, write_config
from flowfield.publication import (
    DEPENDENCY_CHANGED,
    Publication,
    PublicationStatus,
    Readiness,
    blocked_dependencies,
    preparation_issue,
    publication_status,
    readiness,
)
from flowfield.questions import QuestionReference, Questions, blocking_questions, pending_questions
from flowfield.schema import SCHEMA, SCHEMA_VERSION
from flowfield.storage import acquire_lock, initialize, require_current

if TYPE_CHECKING:
    from flowfield.workspaces import WorkspaceRecord

Markdown = Annotated[str, StringConstraints(max_length=200_000)]
TaskDescription = Annotated[str, StringConstraints(max_length=400_020)]
TaskIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[a-z0-9][a-z0-9_-]{0,63}|[Tt]-[1-9][0-9]*)$"),
]
WorkStatus = Literal["backlog", "up_next", "in_progress", "in_review", "done"]
PlanningStatus = Literal["backlog", "up_next"]
PLANNING = ("backlog", "up_next")
LabelIds = Annotated[list[Identifier], Field(max_length=20)]
STATUSES = ("backlog", "up_next", "in_progress", "in_review", "done")


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


def health() -> Health:
    return Health(version=__version__)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectSetup(Input):
    path: str = Field(min_length=1)
    id: Identifier | None = None
    name: Title | None = None
    author: Title = "human"


class ProjectSetupDefaults(Input):
    # Suggestions may be empty for directory names without a usable identifier.
    # Registration still validates the user's required, editable choices.
    id: str
    name: str


def setup_defaults(
    path: Path, config: ProjectConfig | None, existing: "Project | None"
) -> ProjectSetupDefaults:
    identity = (
        config.project_id
        if config
        else existing.id
        if existing
        else re.sub(r"[^a-z0-9_-]+", "-", path.name.lower()).strip("-_")[:64]
    )
    return ProjectSetupDefaults(
        id=identity,
        name=existing.name if existing else config.name if config else path.name[:200],
    )


class Project(Input):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    id: Identifier
    name: Title
    path: str = Field(min_length=1)

    description: str = ""
    worker_documents: DocumentReferences = Field(
        default_factory=lambda: [DocumentReference(id="guidance")], validate_default=True
    )

    @field_validator("worker_documents", mode="before")
    @classmethod
    def stored_documents(cls, value: Any) -> Any:
        references = json.loads(value) if isinstance(value, str) else value
        if not isinstance(references, list):
            return references
        # Apply the shared default to current project settings, never to frozen assignments.
        return (
            references
            if any(
                (
                    item.id
                    if isinstance(item, DocumentReference)
                    else item.get("id")
                    if isinstance(item, dict)
                    else None
                )
                == "guidance"
                for item in references
            )
            else [*references, {"id": "guidance"}]
        )

    revision: int = 1
    created_at: str
    updated_at: str
    updated_by: str


class Edit(Input):
    expected_revision: int = Field(ge=1)
    author: Title = "human"

    @model_validator(mode="after")
    def reject_null_fields(self) -> Self:
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null; omit it to preserve its value.")
        return self


class ProjectEdit(Edit):
    name: Title | None = None
    description: Markdown | None = None
    worker_documents: DocumentReferences | None = None


class WorkCreate(Input):
    title: Title
    id: Identifier | None = None
    body: Markdown = ""
    author: Title = "human"


class ContentRecord(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    id: str
    title: str
    body: str
    revision: int
    created_at: str
    updated_at: str
    updated_by: str


class WorkRecord(ContentRecord):
    workspace_id: str
    project_id: str | None = None


class TaskPreparation(Input):
    completion: Literal["code", "report"]


class TaskCreate(WorkCreate):
    project_id: Identifier | None = None
    acceptance_criteria: Markdown = ""
    input_files: list[Identifier] = Field(default_factory=list, max_length=32)
    preparation: TaskPreparation | None = None
    body: TaskDescription = ""
    labels: LabelIds = Field(default_factory=list)
    status: WorkStatus = "backlog"
    dependencies: list[TaskIdentifier] = Field(default_factory=list)
    documents: DocumentReferences = Field(default_factory=list)

    @field_validator("dependencies", "labels")
    @classmethod
    def normalize_dependencies(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class TaskEdit(Edit):
    acceptance_criteria: Markdown | None = None
    input_files: list[Identifier] | None = Field(default=None, max_length=32)
    preparation: TaskPreparation | None = None
    title: Title | None = None
    body: TaskDescription | None = None
    labels: LabelIds | None = None
    archived: bool | None = None
    dependencies: list[TaskIdentifier] | None = None
    documents: DocumentReferences | None = None

    @field_validator("dependencies", "labels")
    @classmethod
    def normalize_dependencies(cls, value: list[str] | None) -> list[str] | None:
        return sorted(set(value)) if value is not None else None


class TaskPriority(Edit):
    status: PlanningStatus
    before_id: TaskIdentifier | None = None

    @model_validator(mode="after")
    def reject_null_fields(self) -> Self:
        # before_id=null means append to the destination column.
        if self.author is None:
            raise ValueError("author cannot be null")
        return self


class TaskReconcile(Edit):
    completion: Literal["code", "report"] | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200_000)]


class TaskReopen(Edit):
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class TaskPublish(Edit):
    completion: Literal["code", "report"]


class TaskRevision(WorkRecord):
    acceptance_criteria: str = ""
    input_files: list[str] = Field(default_factory=list)

    agreement_revision: int = 1
    publication: Publication | None = None
    key: str
    labels: list[str] = Field(default_factory=list)
    status: WorkStatus
    archived: bool = False
    dependencies: list[str]
    documents: DocumentReferences = Field(default_factory=list)
    reconciliation_reason: str | None = None
    change_note: str = ""


class TaskReference(BaseModel):
    workspace_id: str
    project_id: str | None = None
    id: str
    key: str
    title: str
    status: WorkStatus
    archived: bool
    reconciliation_reason: str | None


class Task(TaskRevision):
    document_updates: list[DocumentUpdate] = Field(default_factory=list)
    preparation_issue: str | None
    latest_update: ActivityEntry | None = None
    position: int
    revisions: list[TaskRevision]
    publication_status: PublicationStatus
    readiness: Readiness
    blocked_by: list[TaskReference]
    blocking_questions: list[QuestionReference]
    prerequisites: list[TaskReference]
    dependents: list[TaskReference]

    def _archive_revision(self) -> TaskRevision | None:
        if self.archived:
            for index in range(len(self.revisions) - 1, -1, -1):
                revision = self.revisions[index]
                if revision.archived and (index == 0 or not self.revisions[index - 1].archived):
                    return revision
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def archived_at(self) -> str | None:
        revision = self._archive_revision()
        return revision.updated_at if revision else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def archived_by(self) -> str | None:
        revision = self._archive_revision()
        return revision.updated_by if revision else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status_changed_at(self) -> str:
        entered = self.created_at
        previous = None
        for revision in self.revisions:
            if revision.status != previous:
                entered = revision.updated_at
                previous = revision.status
        return entered


class Board(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    workspace_id: str
    tasks: list[Task]
    needs_you_count: int = 0
    awaiting_application_count: int = 0


def now() -> str:
    return datetime.now(UTC).isoformat()


def generated_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", title.lower()).strip("-_")[:48] or "task"
    return f"{slug}-{uuid4().hex[:8]}"


class Workspace:
    def __init__(
        self,
        directory: Path,
        *,
        on_change: Callable[[str | None], None] | None = None,
        on_activity: Callable[[str, str], None] | None = None,
        on_coordinator_activity: Callable[[str, str], None] | None = None,
    ):
        from flowfield.integration_models import ValidationProgress

        self.validation_progress: dict[str, ValidationProgress] = {}
        # Successful checks may be reused within this service lifetime. A restart
        # deliberately invalidates reuse after host/toolchain/service changes.
        self.validation_epoch = uuid4().hex
        self.on_coordinator_activity = on_coordinator_activity
        self.on_activity = on_activity
        self.on_change = on_change
        self.directory = directory.expanduser().resolve()
        self.database = self.directory / "workspace.sqlite3"
        self.schema_version = SCHEMA_VERSION
        initialize(self.directory, SCHEMA)
        (self.directory / "artifacts").mkdir(mode=0o700, exist_ok=True)

    @contextmanager
    def connection(
        self, *, write: bool = False, workspace_id: str | None = None, notify: bool = True
    ) -> Iterator[sqlite3.Connection]:
        with acquire_lock(self.directory, ".initialize.lock", shared=True):
            db = sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            if not write:
                db.execute("PRAGMA query_only = ON")
            try:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                require_current(db, self.schema_version)
                yield db
                changed = db.total_changes > 0
                changed_projects = {workspace_id}
                if (
                    workspace_id is not None
                    and db.execute(
                        "SELECT 1 FROM sqlite_temp_master WHERE name='changed_task_workspaces'"
                    ).fetchone()
                ):
                    changed_projects.update(
                        row[0]
                        for row in db.execute("SELECT workspace_id FROM changed_task_workspaces")
                    )
                if write and changed and notify:
                    from flowfield.announcements import reconcile_attention

                    for changed_project in changed_projects:
                        reconcile_attention(db, changed_project)
                if changed:
                    db.execute("UPDATE storage_metadata SET revision=revision+1 WHERE id=1")
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
        if write and changed and notify and self.on_change is not None:
            for changed_project in changed_projects:
                self.on_change(changed_project)

    def _insert_project(self, db: sqlite3.Connection, project: Project) -> None:
        db.execute(
            "INSERT INTO projects (id,name,path,description,revision,created_at,updated_at,"
            "updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project.id,
                project.name,
                project.path,
                project.description,
                project.revision,
                project.created_at,
                project.updated_at,
                project.updated_by,
            ),
        )

    def projects(self) -> list[Project]:
        with self.connection() as db:
            return [
                Project(**dict(row)) for row in db.execute("SELECT * FROM projects ORDER BY id")
            ]

    def _setup_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ApplicationError(
                "invalid_path", "Project setup requires an absolute directory path."
            )
        path = path.resolve()
        if path / ".flowfield" == self.directory:
            raise ApplicationError(
                "invalid_path", "Project config cannot share the global data directory."
            )
        if not path.is_dir():
            raise ApplicationError(
                "invalid_path",
                "Choose an existing project directory. Flowfield does not create projects.",
            )
        return path

    def project_setup_defaults(self, request: ProjectSetup) -> ProjectSetupDefaults:
        path = self._setup_path(request.path)
        config = read_config(path)
        with self.connection() as db:
            row = db.execute("SELECT * FROM projects WHERE path = ?", (str(path),)).fetchone()
            defaults = setup_defaults(path, config, Project(**dict(row)) if row else None)
            return defaults

    def setup_project(self, request: ProjectSetup, *, workspace_id: str) -> Project:
        from flowfield.workspaces import Workspaces

        path = self._setup_path(request.path)
        try:
            with self.connection(write=True) as db:
                if not path.is_dir():
                    raise ApplicationError(
                        "invalid_path",
                        "Choose an existing project directory. Flowfield does not create projects.",
                    )
                config = read_config(path)
                row = db.execute("SELECT * FROM projects WHERE path = ?", (str(path),)).fetchone()
                existing = Project(**dict(row)) if row else None
                if workspace_id is not None:
                    Workspaces.read(db, workspace_id)
                    membership = (
                        db.execute(
                            "SELECT workspace_id FROM workspace_projects WHERE project_id=?",
                            (existing.id,),
                        ).fetchone()
                        if existing
                        else None
                    )
                    if membership and membership[0] != workspace_id:
                        raise ApplicationError(
                            "project_workspace_conflict",
                            "This project already belongs to another workspace.",
                            409,
                        )
                    if existing and membership is None:
                        raise ApplicationError(
                            "project_workspace_conflict",
                            "This project was registered without a workspace. "
                            "Moving registered projects between scopes is not supported.",
                            409,
                        )
                defaults = setup_defaults(path, config, existing)
                identity, name = defaults.id, defaults.name
                if (config or existing) and (
                    (request.id is not None and request.id != identity)
                    or (request.name is not None and request.name != name)
                ):
                    raise ApplicationError(
                        "project_conflict",
                        "This directory already has a different project ID or name. "
                        "Use its saved project details.",
                        409,
                    )
                try:
                    chosen = ProjectConfig(
                        version=1, project_id=request.id or identity, name=request.name or name
                    )
                except ValidationError as error:
                    raise ApplicationError(
                        "invalid_project",
                        "Enter a project name and a valid project ID. "
                        "Use lowercase letters, numbers, hyphens or underscores for the ID.",
                    ) from error
                if existing and existing.id != chosen.project_id:
                    raise ApplicationError(
                        "project_conflict",
                        "Project config conflicts with the registered project. "
                        "Neither was changed.",
                        409,
                    )
                other = db.execute(
                    "SELECT path FROM projects WHERE id = ?", (chosen.project_id,)
                ).fetchone()
                if other and other["path"] != str(path):
                    raise ApplicationError(
                        "project_conflict",
                        "This project ID is already used by another directory. "
                        "Choose a different project ID.",
                        409,
                    )
                # Validate identity before creating files. A valid config left by an
                # interrupted registration can be registered by repeating init.
                if existing:
                    if config is None:
                        write_config(path, chosen)
                    if workspace_id is not None:
                        db.execute(
                            "INSERT OR IGNORE INTO workspace_projects VALUES (?,?)",
                            (existing.id, workspace_id),
                        )
                    return existing
                project = Project(
                    id=chosen.project_id,
                    name=chosen.name,
                    path=str(path),
                    created_at=now(),
                    updated_at=now(),
                    updated_by=request.author,
                )
                self._insert_project(db, project)
                if workspace_id is not None:
                    db.execute(
                        "INSERT INTO workspace_projects VALUES (?,?)", (project.id, workspace_id)
                    )
                if config is None:
                    write_config(path, chosen)
                return project
        except OSError as error:
            raise ApplicationError(
                "project_setup_failed",
                f"Could not add the project at {path}: {error}. "
                "Check directory permissions and try again. Existing files were not overwritten.",
            ) from error

    def _project(self, db: sqlite3.Connection, project_id: str) -> Project:
        row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise ApplicationError("not_found", "Project not found.", 404)
        return Project(**dict(row))

    def project(self, project_id: str) -> Project:
        with self.connection() as db:
            return self._project(db, project_id)

    @staticmethod
    def _workspace(db: sqlite3.Connection, workspace_id: str) -> "WorkspaceRecord":
        from flowfield.workspaces import Workspaces

        return Workspaces.read(db, workspace_id)

    def _patch(self, current: BaseModel, request: Edit) -> dict[str, Any]:
        values = current.model_dump()
        self._current(values["revision"], request.expected_revision)
        changes = request.model_dump(exclude_unset=True, exclude={"expected_revision", "author"})
        if all(values[key] == value for key, value in changes.items()):
            return values
        return {
            **values,
            **changes,
            "revision": values["revision"] + 1,
            "updated_at": now(),
            "updated_by": request.author,
        }

    def edit_project(self, project_id: str, request: ProjectEdit) -> Project:
        with self.connection(write=True) as db:
            return self._edit_project(db, project_id, request)

    def _edit_project(
        self, db: sqlite3.Connection, project_id: str, request: ProjectEdit
    ) -> Project:
        current = self._project(db, project_id)
        project = Project(**self._patch(current, request))
        if request.worker_documents is not None:
            from flowfield.worker_documents import validate_references

            validate_references(
                db, self._project_workspace(db, project_id), project.worker_documents
            )
        db.execute(
            "UPDATE projects SET name=?, description=?, revision=?, updated_at=?, "
            "updated_by=? WHERE id=?",
            (
                project.name,
                project.description,
                project.revision,
                project.updated_at,
                project.updated_by,
                project_id,
            ),
        )
        db.execute(
            "UPDATE projects SET worker_documents=? WHERE id=?",
            (json.dumps([item.model_dump() for item in project.worker_documents]), project_id),
        )
        return project

    @staticmethod
    def _project_workspace(db: sqlite3.Connection, project_id: str) -> str:
        row = db.execute(
            "SELECT workspace_id FROM workspace_projects WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            raise ApplicationError(
                "workspace_required", "Add this project to an explicit workspace first.", 409
            )
        return str(row[0])

    def _record_key(self, task: TaskRevision) -> str:
        return task.key

    def _record_rows(
        self, db: sqlite3.Connection, workspace_id: str, *, metadata: bool = False
    ) -> list[sqlite3.Row]:
        data = "json_set(t.data,'$.body','','$.change_note','')" if metadata else "t.data"
        self._workspace(db, workspace_id)
        return db.execute(
            f"SELECT t.id,t.key,t.position,{data} AS data FROM tasks t WHERE t.workspace_id=?",
            (workspace_id,),
        ).fetchall()

    def _records(self, db: sqlite3.Connection, workspace_id: str) -> dict[str, TaskRevision]:
        return {
            self._record_key(task): task
            for row in self._record_rows(db, workspace_id, metadata=True)
            for task in [TaskRevision.model_validate_json(row["data"])]
        }

    def _dependency_ids(
        self, db: sqlite3.Connection, workspace_id: str, references: list[str]
    ) -> list[str]:
        self._workspace(db, workspace_id)
        rows = db.execute(
            "SELECT t.id,t.key,t.workspace_id FROM tasks t WHERE t.workspace_id=?",
            (workspace_id,),
        ).fetchall()
        local = {r["id"]: r["key"] for r in rows if r["workspace_id"] == workspace_id}
        workspace_keys = {r["key"] for r in rows}
        return sorted(
            {
                local.get(ref, ref.upper() if ref.upper() in workspace_keys else ref)
                for ref in references
            }
        )

    def _dependency_order(self, records: dict[str, TaskRevision]) -> list[str]:
        remaining = {key: len(task.dependencies) for key, task in records.items()}
        children: dict[str, list[str]] = {key: [] for key in records}
        for key, task in records.items():
            for prerequisite in task.dependencies:
                if (
                    prerequisite == key
                    or prerequisite not in records
                    or (records[prerequisite].archived and records[prerequisite].status != "done")
                ):
                    raise ApplicationError(
                        "invalid_dependency",
                        "Choose another task in this workspace as a prerequisite.",
                    )
                children[prerequisite].append(key)
        order = [key for key, count in remaining.items() if count == 0]
        for key in order:
            for child in children[key]:
                remaining[child] -= 1
                if remaining[child] == 0:
                    order.append(child)
        if len(order) != len(records):
            raise ApplicationError(
                "dependency_cycle", "These prerequisites would create a dependency cycle."
            )
        return order

    def _completed(self, records: dict[str, TaskRevision], db: sqlite3.Connection) -> set[str]:
        """Completion belongs to the task's delivery, not its ancestors' later work."""
        completed: set[str] = set()
        for key, task in records.items():
            if (
                task.status == "done"
                and db.execute(
                    "SELECT 1 FROM result_versions v JOIN runs r ON r.id=v.run_id "
                    "LEFT JOIN task_revisions d ON d.workspace_id=v.workspace_id "
                    "AND d.task_id=v.task_id "
                    "AND d.revision=json_extract(v.data,'$.reconciled_task_revision') "
                    "WHERE v.workspace_id=? AND v.task_id=? AND v.status='delivered' "
                    "AND coalesce(json_extract(d.data,'$.agreement_revision'),"
                    "json_extract(r.data,'$.agreement_revision'))=? "
                    "AND NOT EXISTS (SELECT 1 FROM result_versions n WHERE "
                    "n.workspace_id=v.workspace_id AND n.task_id=v.task_id AND "
                    "n.version>v.version)",
                    (task.workspace_id, task.id, task.agreement_revision),
                ).fetchone()
                and not task.reconciliation_reason
                and publication_status(task) != "needs_reconciliation"
            ):
                completed.add(key)
        return completed

    def _apply_task(
        self, db: sqlite3.Connection, task: TaskRevision, *, reconciled: bool = False
    ) -> None:
        """Validate and persist the edit and its downstream impacts in one transaction."""
        self._apply_tasks(db, [task], reconciled=reconciled)

    def _apply_tasks(
        self, db: sqlite3.Connection, tasks: list[TaskRevision], *, reconciled: bool = False
    ) -> None:
        """Apply a checked batch before deriving downstream impacts, once per revision."""
        if not tasks:
            return
        first = tasks[0]
        records = self._records(db, first.workspace_id)
        changed = {self._record_key(task) for task in tasks}
        for task in tasks:
            if task.input_files:
                from flowfield.task_files import freeze

                freeze(db, task.workspace_id, task.input_files)
            if self._record_key(task) not in records:
                raise ApplicationError("invalid_dependency", "Task is outside this workspace.")
            previous_row = db.execute(
                "SELECT data FROM tasks WHERE workspace_id=? AND id=?",
                (task.workspace_id, task.id),
            ).fetchone()
            previous = TaskRevision.model_validate_json(previous_row[0]) if previous_row else None
            if previous and (
                previous.body != task.body
                or previous.acceptance_criteria != task.acceptance_criteria
                or previous.dependencies != task.dependencies
                or previous.documents != task.documents
                or previous.input_files != task.input_files
            ):
                task.agreement_revision = previous.agreement_revision + 1
                if task.status not in PLANNING and not reconciled:
                    task.reconciliation_reason = (
                        "Requirements changed. Review recorded work with the coordinator."
                    )
                    task.change_note = task.reconciliation_reason
            if reconciled:
                assert task.publication
                task.reconciliation_reason = None
                task.publication = task.publication.model_copy(
                    update={
                        "task_revision": task.revision,
                        "agreement_revision": task.agreement_revision,
                    }
                )
            records[self._record_key(task)] = task
        completed: set[str] = set()
        affected = set(changed)
        for key in self._dependency_order(records):
            item = records[key]
            if any(prerequisite in affected for prerequisite in item.dependencies):
                affected.add(key)
            blocked = [p for p in item.dependencies if p not in completed]
            if (
                blocked
                and item.status in ("in_progress", "in_review")
                and not item.reconciliation_reason
            ):
                reason = DEPENDENCY_CHANGED
                if key not in changed:
                    item = TaskRevision.model_validate_json(
                        db.execute(
                            "SELECT data FROM tasks WHERE workspace_id=? AND id=?",
                            (item.workspace_id, item.id),
                        ).fetchone()[0]
                    )
                item = item.model_copy(
                    update={
                        "reconciliation_reason": reason,
                        "change_note": reason,
                        "revision": item.revision + (key not in changed),
                        "updated_at": first.updated_at,
                        "updated_by": first.updated_by,
                    }
                )
                records[key] = item
                changed.add(key)
            if (
                item.status == "done"
                and not item.reconciliation_reason
                and publication_status(item) != "needs_reconciliation"
            ):
                completed.add(key)
        self._mark_task_workspaces(db, {records[key].workspace_id for key in affected})
        for key in changed:
            self._save_task(db, records[key])

    def _task(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        task_id: str,
        records: dict[str, TaskRevision] | None = None,
        completed: set[str] | None = None,
    ) -> Task:
        row = db.execute(
            "SELECT * FROM tasks WHERE workspace_id=? "
            "AND (id=? OR key=? COLLATE NOCASE) ORDER BY id=? DESC",
            (workspace_id, task_id, task_id, task_id),
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Task not found in this workspace.", 404)
        current = TaskRevision.model_validate_json(row["data"])
        task_id = current.id
        revisions = [
            TaskRevision.model_validate_json(r[0])
            for r in db.execute(
                "SELECT data FROM task_revisions WHERE workspace_id=? AND task_id=? "
                "ORDER BY revision",
                (workspace_id, task_id),
            )
        ]
        records = records if records is not None else self._records(db, workspace_id)
        completed = completed if completed is not None else self._completed(records, db)
        blocked = [records[p] for p in blocked_dependencies(current, completed)]
        questions = blocking_questions(db, workspace_id, task_id)
        publication = publication_status(current)
        from flowfield.document_updates import task_updates

        return Task(
            **current.model_dump(),
            position=row["position"],
            document_updates=task_updates(db, workspace_id, task_id)
            if current.publication and current.publication.documents
            else [],
            latest_update=self._latest_update(db, workspace_id, task_id),
            revisions=revisions,
            publication_status=publication,
            preparation_issue=preparation_issue(db, current),
            readiness=readiness(
                current,
                publication,
                blocked=bool(blocked or questions),
            ),
            blocked_by=[TaskReference(**item.model_dump()) for item in blocked],
            blocking_questions=questions,
            prerequisites=[TaskReference(**records[p].model_dump()) for p in current.dependencies],
            dependents=[
                TaskReference(**item.model_dump())
                for item in records.values()
                if self._record_key(current) in item.dependencies
            ],
        )

    def task(self, workspace_id: str, task_id: str) -> Task:
        with self.connection() as db:
            return self._task(db, workspace_id, task_id)

    def _tasks(
        self, db: sqlite3.Connection, workspace_id: str, include_archived: bool
    ) -> list[Task]:
        records = self._records(db, workspace_id)
        completed = self._completed(records, db)
        tasks = [
            self._task(db, workspace_id, row[0], records, completed)
            for row in db.execute(
                "SELECT id FROM tasks WHERE workspace_id=? ORDER BY position, id", (workspace_id,)
            ).fetchall()
        ]
        return sorted(
            (t for t in tasks if include_archived or not t.archived),
            key=lambda t: (
                STATUSES.index(t.status),
                t.position
                if t.status in PLANNING
                else (
                    datetime.fromisoformat(t.status_changed_at).timestamp()
                    * (-1 if t.status == "done" else 1)
                ),
                t.id,
            ),
        )

    def tasks(self, workspace_id: str, *, include_archived: bool = False) -> list[Task]:
        with self.connection() as db:
            self._workspace(db, workspace_id)
            return self._tasks(db, workspace_id, include_archived)

    def board(self, workspace_id: str) -> Board:
        with self.connection() as db:
            return Board(
                workspace_id=workspace_id,
                tasks=self._tasks(db, workspace_id, True),
                **Questions(self).counts(db, workspace_id),
            )

    @staticmethod
    def _mark_task_workspaces(db: sqlite3.Connection, projects: set[str]) -> None:
        # Include inferred readiness changes as well as saved revisions. Temporary
        # tracking lets the transaction owner notify affected views only after commit.
        db.execute(
            "CREATE TEMP TABLE IF NOT EXISTS changed_task_workspaces (workspace_id TEXT "
            "PRIMARY KEY)"
        )
        db.executemany(
            "INSERT OR IGNORE INTO changed_task_workspaces VALUES (?)",
            [(project,) for project in projects],
        )

    def _save_task(self, db: sqlite3.Connection, task: TaskRevision) -> None:
        self._mark_task_workspaces(db, {task.workspace_id})
        previous_row = db.execute(
            "SELECT data FROM task_revisions WHERE workspace_id=? AND task_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (task.workspace_id, task.id),
        ).fetchone()
        previous = TaskRevision.model_validate_json(previous_row[0]) if previous_row else None
        messages = []
        if previous is None:
            messages.append(f"Task created in {task.status.replace('_', ' ').capitalize()}.")
        else:
            for field, label in (
                ("body", "Description"),
                ("acceptance_criteria", "Acceptance criteria"),
                ("title", "Title"),
                ("labels", "Labels"),
                ("dependencies", "Prerequisites"),
                ("documents", "Worker documents"),
            ):
                if getattr(previous, field) != getattr(task, field):
                    messages.append(f"{label} updated.")
            if previous.publication != task.publication:
                messages.append("Assignment checked and published.")
            elif previous.agreement_revision != task.agreement_revision and previous.publication:
                messages.append(
                    "Assignment changed; coordinator must republish before work starts."
                    if task.status in PLANNING
                    else "Assignment changed; recorded work needs reconciliation."
                )
            if previous.status != task.status:
                messages.append(
                    f"{previous.status.replace('_', ' ').capitalize()} → "
                    f"{task.status.replace('_', ' ').capitalize()}."
                )
            if previous.archived != task.archived:
                messages.append("Task archived." if task.archived else "Task restored.")
            if previous.reconciliation_reason != task.reconciliation_reason:
                messages.append(task.change_note or "Recorded work reviewed.")
        if messages:
            self._insert_activity(
                db,
                task.workspace_id,
                task.id,
                uuid4().hex,
                "event",
                "\n\n".join(messages),
                task.updated_by,
                task.updated_at,
                task_revision=task.revision,
            )
        db.execute(
            "DELETE FROM task_dependencies WHERE workspace_id=? AND task_id=?",
            (task.workspace_id, task.id),
        )
        db.executemany(
            "INSERT INTO task_dependencies(workspace_id,task_id,prerequisite_id) "
            "SELECT ?,?,id FROM tasks WHERE workspace_id=? AND key=?",
            [(task.workspace_id, task.id, task.workspace_id, key) for key in task.dependencies],
        )
        data = task.model_dump_json()
        db.execute(
            "UPDATE tasks SET data=?,scheduling_problem=NULL WHERE workspace_id=? AND id=?",
            (data, task.workspace_id, task.id),
        )
        db.execute(
            "INSERT INTO task_revisions VALUES (?, ?, ?, ?)",
            (task.workspace_id, task.id, task.revision, data),
        )

    def _append_position(self, db: sqlite3.Connection, workspace_id: str) -> int:
        return int(
            db.execute(
                "SELECT coalesce(max(t.position),-1)+1 FROM tasks t WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )

    def create_task(self, workspace_id: str, request: TaskCreate) -> Task:
        if request.status == "done":
            raise ApplicationError(
                "completion_required",
                "Create the task, then accept a report or deliver its code before marking Done.",
                409,
            )
        with self.connection(write=True, workspace_id=workspace_id) as db:
            self._workspace(db, workspace_id)
            if request.project_id:
                from flowfield.workspaces import Workspaces

                Workspaces.check_project(db, workspace_id, request.project_id)
            if request.documents:
                from flowfield.worker_documents import validate_references

                validate_references(db, workspace_id, request.documents)
            from flowfield.labels import Labels

            Labels.validate(db, workspace_id, request.labels)
            number = db.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM ("
                "SELECT number FROM tasks WHERE workspace_id=? UNION ALL "
                "SELECT number FROM task_deletions WHERE workspace_id=?)",
                (workspace_id, workspace_id),
            ).fetchone()[0]
            task = TaskRevision(
                **request.model_dump(exclude={"id", "author", "dependencies", "preparation"}),
                key=f"T-{number}",
                dependencies=self._dependency_ids(db, workspace_id, request.dependencies),
                id=request.id or generated_id(request.title),
                workspace_id=workspace_id,
                revision=1,
                created_at=now(),
                updated_at=now(),
                updated_by=request.author,
            )
            if db.execute(
                "SELECT 1 FROM task_deletions WHERE workspace_id=? AND task_id=?",
                (workspace_id, task.id),
            ).fetchone():
                raise ApplicationError(
                    "task_deleted", "This task ID was deleted; choose a new ID.", 409
                )
            try:
                db.execute(
                    "INSERT INTO tasks(workspace_id,id,position,data,number,key,project_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        workspace_id,
                        task.id,
                        self._append_position(db, workspace_id),
                        task.model_dump_json(),
                        number,
                        task.key,
                        task.project_id,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ApplicationError(
                    "task_exists", "Task ID already exists in this workspace.", 409
                ) from error
            records = self._records(db, workspace_id)
            completed = self._completed(records, db)
            if task.status not in PLANNING and any(p not in completed for p in task.dependencies):
                raise ApplicationError(
                    "task_blocked", "Complete prerequisites before recording work.", 409
                )
            self._apply_task(db, task)
            if request.preparation:
                return self._publish_task(
                    db,
                    workspace_id,
                    task.id,
                    TaskPublish(
                        **request.preparation.model_dump(),
                        expected_revision=task.revision,
                        author=request.author,
                    ),
                )
            return self._task(db, workspace_id, task.id)

    def edit_task(self, workspace_id: str, task_id: str, request: TaskEdit) -> Task:
        preparation = request.preparation
        request = TaskEdit.model_validate(
            request.model_dump(exclude_unset=True, exclude={"preparation"})
        )
        with self.connection(write=True, workspace_id=workspace_id) as db:
            current = self._task(db, workspace_id, task_id)
            task_id = current.id
            if request.documents is not None:
                from flowfield.worker_documents import validate_references

                validate_references(db, workspace_id, request.documents)
            if request.labels is not None:
                from flowfield.labels import Labels

                Labels.validate(db, workspace_id, request.labels)
            if request.dependencies is not None:
                request = request.model_copy(
                    update={
                        "dependencies": self._dependency_ids(db, workspace_id, request.dependencies)
                    }
                )
            values = self._patch(current, request)
            task = TaskRevision(**values)
            task.change_note = ""
            if task.archived and not current.archived:
                issue = self.archive_issue(db, current)
                if issue:
                    raise issue
                questions = Questions(self)
                for reference in pending_questions(db, workspace_id, task_id):
                    if reference.task_id != task_id:
                        continue
                    question = questions._get(db, workspace_id, reference.id)
                    questions._save(
                        db,
                        questions._change(
                            question,
                            question.revision,
                            request.author,
                            status="withdrawn",
                            decision="Task archived.",
                        ),
                        "Question withdrawn because the task was archived.",
                    )
                db.execute(
                    "UPDATE task_replies SET data=json_set(data,'$.status','cancelled') "
                    "WHERE workspace_id=? AND task_id=? "
                    "AND json_extract(data,'$.status')='pending'",
                    (workspace_id, task_id),
                )
            if task.revision != current.revision:
                self._apply_task(db, task)
                if current.archived and not task.archived:
                    db.execute(
                        "UPDATE tasks SET position=? WHERE workspace_id=? AND id=?",
                        (self._append_position(db, workspace_id), workspace_id, task_id),
                    )
            if preparation:
                return self._publish_task(
                    db,
                    workspace_id,
                    task_id,
                    TaskPublish.model_validate(
                        {
                            **preparation.model_dump(),
                            "expected_revision": task.revision,
                            "author": request.author,
                        }
                    ),
                )
            return self._task(db, workspace_id, task_id)

    def archive_issue(self, db: sqlite3.Connection, task: TaskRevision) -> ApplicationError | None:
        """One archive policy for mutation and browser availability; recheck on write."""
        if task.archived:
            return None
        if any(q.task_id != task.id for q in pending_questions(db, task.workspace_id, task.id)):
            return ApplicationError(
                "pending_questions", "Resolve pending questions before archiving.", 409
            )
        if db.execute(
            "SELECT 1 FROM runs WHERE workspace_id=? AND task_id=? AND status IN (?,?,?,?)",
            (task.workspace_id, task.id, *ACTIVE),
        ).fetchone():
            return ApplicationError(
                "worker_owns_work", "Finish or stop the worker before archiving.", 409
            )
        for table, statuses in (
            ("task_reviews", "'preparing','running','uncertain'"),
            ("result_versions", "'preparing','delivering'"),
            ("integrations", "'preparing','applying'"),
        ):
            task_column = "json_extract(data,'$.task_id')" if table == "integrations" else "task_id"
            if db.execute(
                f"SELECT 1 FROM {table} WHERE workspace_id=? AND {task_column}=? "
                f"AND status IN ({statuses}) LIMIT 1",
                (task.workspace_id, task.id),
            ).fetchone():
                return ApplicationError(
                    "result_owns_work",
                    "Stop active checks or review, or wait for integration before archiving.",
                    409,
                )
        return None

    def publish_task(self, workspace_id: str, task_id: str, request: TaskPublish) -> Task:
        with self.connection(write=True, workspace_id=workspace_id) as db:
            return self._publish_task(db, workspace_id, task_id, request)

    def _publish_task(
        self, db: sqlite3.Connection, workspace_id: str, task_id: str, request: TaskPublish
    ) -> Task:
        current = self._task(db, workspace_id, task_id)
        self._current(current.revision, request.expected_revision)
        if current.archived or current.status not in PLANNING:
            raise ApplicationError(
                "inactive_task",
                "Publish upcoming work; reconcile work already in progress or completed.",
                409,
            )
        if not current.body.strip():
            raise ApplicationError(
                "missing_description",
                "Describe the outcome, completion expectations and relevant context first.",
                409,
            )
        if current.blocking_questions:
            raise ApplicationError(
                "task_blocked",
                "Apply or withdraw blocking questions before publishing the task.",
                409,
            )
        from flowfield.subtasks import Subtasks

        if request.completion == "report" and any(
            child.completion == "code" for child in Subtasks.read_all(db, workspace_id, current.id)
        ):
            raise ApplicationError(
                "code_delivery_required", "A task with code contributions requires code delivery."
            )
        documents = self._prepare_sources(db, current, request.author)
        current = self._task(db, workspace_id, current.id)
        from flowfield.integration import Integrations

        target_branch = (
            Integrations(self)._settings(db, current.project_id).target_branch
            if request.completion == "code" and current.project_id
            else None
        )
        if request.completion == "code":
            if current.project_id is None:
                raise ApplicationError(
                    "repository_required",
                    "Workspace tasks deliver files and reports. "
                    "Choose an existing project for repository changes.",
                    409,
                )
            if not target_branch:
                raise ApplicationError(
                    "integration_target_required",
                    "Configure the delivery target and checks before publishing code work.",
                    409,
                )
        if (
            current.publication_status == "published"
            and current.publication
            and current.publication.completion == request.completion
            and current.publication.target_branch == target_branch
            and current.publication.documents == documents
        ):
            return current
        task = TaskRevision(
            **{
                **current.model_dump(),
                "revision": current.revision + 1,
                "updated_at": now(),
                "updated_by": request.author,
                "change_note": "",
            }
        )
        task.publication = Publication(
            documents=documents,
            completion=request.completion,
            target_branch=target_branch,
            agreement_revision=current.agreement_revision,
            task_revision=current.revision,
            author=request.author,
            created_at=task.updated_at,
        )
        self._apply_task(db, task)
        return self._task(db, workspace_id, current.id)

    def _prepare_sources(
        self, db: sqlite3.Connection, current: Task, author: str
    ) -> list[DocumentSnapshot]:
        from flowfield.guidance_reconciliation import latest_documents

        documents = latest_documents(self, db, current)
        # A newly adopted revision is a scope change, even if the prose description
        # stayed the same. Old runs/results can never satisfy this newer agreement.
        if (
            current.publication
            and current.publication.documents != documents
            and current.publication.agreement_revision == current.agreement_revision
        ):
            task = TaskRevision(
                **{
                    **current.model_dump(),
                    "revision": current.revision + 1,
                    "agreement_revision": current.agreement_revision + 1,
                    "updated_at": now(),
                    "updated_by": author,
                }
            )
            self._apply_task(db, task)
        return documents

    def prioritize_task(self, workspace_id: str, task_id: str, request: TaskPriority) -> Task:
        with self.connection(write=True, workspace_id=workspace_id) as db:
            current = self._task(db, workspace_id, task_id)
            task_id = current.id
            if request.before_id is not None:
                request = request.model_copy(
                    update={
                        "before_id": self._dependency_ids(db, workspace_id, [request.before_id])[0]
                    }
                )
            self._current(current.revision, request.expected_revision)
            if current.archived:
                raise ApplicationError("archived_task", "Restore the task before moving it.", 409)
            if current.status not in PLANNING:
                raise ApplicationError(
                    "active_task",
                    "Only Backlog and Up next can be prioritized. "
                    "Reconcile progress with the coordinator.",
                    409,
                )
            rows = self._record_rows(db, workspace_id, metadata=True)
            ordered = [
                TaskRevision.model_validate_json(row["data"])
                for row in sorted(rows, key=lambda row: (row["position"], row["key"]))
            ]
            scope = {item.key: item for item in ordered}
            identity = current.key
            destination = [
                key
                for key, item in scope.items()
                if not item.archived and item.status == request.status and key != identity
            ]
            if request.before_id is not None and request.before_id not in destination:
                raise ApplicationError(
                    "invalid_order", "Choose another active task in the destination column."
                )
            index = destination.index(request.before_id) if request.before_id else len(destination)
            destination.insert(index, identity)
            previous = [
                key
                for key, item in scope.items()
                if not item.archived and item.status == request.status
            ]
            if current.status == request.status and previous == destination:
                return current
            task = TaskRevision(
                **{
                    **current.model_dump(),
                    "status": request.status,
                    "revision": current.revision + 1,
                    "updated_at": now(),
                    "updated_by": request.author,
                    "change_note": "",
                }
            )
            self._apply_task(db, task)
            # Renumbering neighbors changes order metadata, not their assignments.
            db.executemany(
                "UPDATE tasks SET position=? WHERE workspace_id=? AND id=?",
                [(i, scope[key].workspace_id, scope[key].id) for i, key in enumerate(destination)],
            )
            self._mark_task_workspaces(db, {scope[key].workspace_id for key in destination})
            return self._task(db, workspace_id, task_id)

    def _require_ready(self, task: Task, *, reconciling: bool = False) -> None:
        if task.blocking_questions or task.blocked_by:
            raise ApplicationError(
                "task_blocked", "Resolve task questions and prerequisites first.", 409
            )
        if task.status in PLANNING and task.publication_status == "draft":
            raise ApplicationError(
                "publication_required", "Prepare the current task before continuing.", 409
            )
        if (
            task.reconciliation_reason or task.publication_status == "needs_reconciliation"
        ) and not reconciling:
            raise ApplicationError(
                "reconciliation_required", "Reconcile the changed task before continuing.", 409
            )

    def reopen_task(self, workspace_id: str, task_id: str, request: TaskReopen) -> Task:
        """Prepare a new assignment without rewriting delivered evidence or starting work."""
        with self.connection(write=True, workspace_id=workspace_id) as db:
            current = self._task(db, workspace_id, task_id)
            self._current(current.revision, request.expected_revision)
            if current.archived or current.status != "done":
                raise ApplicationError(
                    "task_not_completed", "Reopen an unarchived completed task for follow-up.", 409
                )
            if (
                db.execute(
                    "SELECT 1 FROM runs WHERE workspace_id=? AND task_id=? "
                    "AND status IN ('preparing','running','stopping','uncertain')",
                    (workspace_id, current.id),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM result_versions WHERE workspace_id=? AND task_id=? "
                    "AND status IN ('preparing','delivering')",
                    (workspace_id, current.id),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM task_replies WHERE workspace_id=? AND task_id=? "
                    "AND json_extract(data,'$.status')='pending'",
                    (workspace_id, current.id),
                ).fetchone()
            ):
                raise ApplicationError(
                    "task_busy", "Wait for the current execution or delivery before reopening.", 409
                )
            task = TaskRevision(
                **{
                    **current.model_dump(),
                    "status": "backlog",
                    "revision": current.revision + 1,
                    "agreement_revision": current.agreement_revision + 1,
                    "reconciliation_reason": None,
                    "updated_at": now(),
                    "updated_by": request.author,
                    "change_note": f"Reopened for follow-up: {request.note}",
                }
            )
            self._apply_task(db, task)
            db.execute(
                "UPDATE tasks SET position=? WHERE workspace_id=? AND id=?",
                (self._append_position(db, workspace_id), workspace_id, current.id),
            )
            return self._task(db, workspace_id, current.id)

    def reconcile_task(self, workspace_id: str, task_id: str, request: TaskReconcile) -> Task:
        with self.connection(write=True, workspace_id=workspace_id) as db:
            current = self._task(db, workspace_id, task_id)
            self._current(current.revision, request.expected_revision)
            if current.archived:
                raise ApplicationError(
                    "archived_task", "Restore the task before reconciling it.", 409
                )
            self._require_ready(current, reconciling=True)
            if (
                not current.reconciliation_reason
                and current.publication_status != "needs_reconciliation"
                and request.completion is None
            ):
                raise ApplicationError(
                    "no_reconciliation", "This task does not need reconciliation.", 409
                )
            completion = request.completion or (
                current.publication.completion if current.publication else None
            )
            from flowfield.integration import Integrations

            target_branch = (
                Integrations(self)._settings(db, current.project_id).target_branch
                if completion == "code" and current.project_id
                else None
            )
            if completion == "code":
                if not target_branch:
                    raise ApplicationError(
                        "integration_target_required",
                        "Configure the delivery target before reconciling code completion.",
                        409,
                    )
            documents = self._prepare_sources(db, current, request.author)
            current = self._task(db, workspace_id, task_id)
            task = TaskRevision(
                **{
                    **current.model_dump(),
                    "revision": current.revision + 1,
                    "updated_at": now(),
                    "updated_by": request.author,
                    "reconciliation_reason": None,
                    "change_note": request.note,
                    "publication": Publication(
                        documents=documents,
                        completion=completion,
                        target_branch=target_branch,
                        agreement_revision=current.agreement_revision,
                        task_revision=current.revision,
                        author=request.author,
                        created_at=now(),
                    )
                    if completion
                    else None,
                }
            )
            self._apply_task(db, task)
            return self._task(db, workspace_id, task_id)

    def _activity_entry(self, db: sqlite3.Connection, entry_id: str) -> ActivityEntry:
        row = db.execute(ACTIVITY_SELECT + " WHERE a.id=?", (entry_id,)).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Activity entry not found.", 404)
        return ActivityEntry(**dict(row))

    def _insert_activity(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        task_id: str | None,
        entry_id: str,
        kind: EntryKind,
        body: str,
        author: str,
        created_at: str,
        supersedes: str | None = None,
        task_revision: int | None = None,
        question_id: str | None = None,
    ) -> ActivityEntry:
        db.execute(
            "INSERT INTO activity (id, workspace_id, task_id, kind, body, author, created_at, "
            "supersedes, task_revision, question_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                workspace_id,
                task_id,
                kind,
                body,
                author,
                created_at,
                supersedes,
                task_revision,
                question_id,
            ),
        )
        return self._activity_entry(db, entry_id)

    def _latest_update(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        task_id: str,
    ) -> ActivityEntry | None:
        row = db.execute(
            ACTIVITY_SELECT + " WHERE a.workspace_id=? AND a.task_id=? AND a.kind!='event' "
            "ORDER BY a.sequence DESC LIMIT 1",
            (workspace_id, task_id),
        ).fetchone()
        return ActivityEntry(**dict(row)) if row else None

    def _activity_scope(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        task_id: str | None,
    ) -> str | None:
        self._workspace(db, workspace_id)
        if task_id is None:
            return None
        row = db.execute(
            "SELECT id FROM tasks WHERE workspace_id=? "
            "AND (id=? OR key=? COLLATE NOCASE) ORDER BY id=? DESC",
            (workspace_id, task_id, task_id, task_id),
        ).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Task not found in this workspace.", 404)
        return str(row[0])

    def activity(
        self,
        workspace_id: str,
        *,
        task_id: str | None = None,
        kind: EntryKind | None = None,
        current_only: bool = False,
        before: int | None = None,
        limit: int = 50,
    ) -> ActivityPage:
        if not 1 <= limit <= 100 or (before is not None and before < 1):
            raise ApplicationError(
                "invalid_request", "Limit must be 1–100; cursor must be positive."
            )
        with self.connection() as db:
            task_id = self._activity_scope(db, workspace_id, task_id)
            sql = ACTIVITY_SELECT + " WHERE a.workspace_id=? AND a.task_id IS ?"
            args: list[Any] = [workspace_id, task_id]
            if kind is not None:
                sql += " AND a.kind=?"
                args.append(kind)
            if current_only:
                sql += " AND a.kind != 'event' AND replacement.id IS NULL"
            if before is not None:
                sql += " AND a.sequence<?"
                args.append(before)
            rows = db.execute(
                sql + " ORDER BY a.sequence DESC LIMIT ?", [*args, limit + 1]
            ).fetchall()
            items = [ActivityEntry(**dict(row)) for row in rows[:limit]]
            return ActivityPage(
                items=items, next_cursor=items[-1].sequence if len(rows) > limit else None
            )

    def activity_entry(self, workspace_id: str, entry_id: str) -> ActivityEntry:
        with self.connection() as db:
            entry = self._activity_entry(db, entry_id)
            if entry.workspace_id != workspace_id:
                raise ApplicationError(
                    "not_found", "Activity entry not found in this project.", 404
                )
            return entry

    def add_activity(self, workspace_id: str, request: ActivityCreate) -> ActivityEntry:
        with self.connection(write=True, workspace_id=workspace_id) as db:
            task_id = self._activity_scope(db, workspace_id, request.task_id)
            # Stable request IDs make a retry after a lost response safe.
            if db.execute("SELECT 1 FROM activity WHERE id=?", (request.id,)).fetchone():
                existing = self._activity_entry(db, request.id)
                if (
                    existing.workspace_id,
                    existing.task_id,
                    existing.kind,
                    existing.body,
                    existing.author,
                    existing.supersedes,
                    existing.task_revision,
                ) == (
                    workspace_id,
                    task_id,
                    request.kind,
                    request.body,
                    request.author,
                    request.supersedes,
                    request.expected_task_revision,
                ):
                    return existing
                raise ApplicationError("activity_conflict", "This entry ID was already used.", 409)
            if request.kind == "handoff":
                row = db.execute(
                    "SELECT json_extract(data, '$.revision') FROM tasks "
                    "WHERE workspace_id=? AND id=?",
                    (workspace_id, task_id),
                ).fetchone()
                latest = db.execute(
                    "SELECT id FROM activity WHERE workspace_id=? AND task_id=? AND kind='handoff' "
                    "ORDER BY sequence DESC LIMIT 1",
                    (workspace_id, task_id),
                ).fetchone()
                if (
                    row[0] != request.expected_task_revision
                    or (latest[0] if latest else None) != request.supersedes
                ):
                    raise ApplicationError(
                        "handoff_conflict",
                        "Task or selected handoff changed. "
                        "Read the task before recording a handoff.",
                        409,
                    )
            return self._insert_activity(
                db,
                workspace_id,
                task_id,
                request.id,
                request.kind,
                request.body,
                request.author,
                now(),
                request.supersedes,
                task_revision=request.expected_task_revision,
            )

    def _current(self, revision: int, expected: int) -> None:
        if revision != expected:
            raise ApplicationError(
                "revision_conflict",
                f"Revision {expected} is stale; current revision is {revision}. "
                "Reload and reconcile before editing.",
                409,
            )
