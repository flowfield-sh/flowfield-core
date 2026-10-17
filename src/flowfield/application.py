"""Service-owned project board operations with guarded storage maintenance."""

import json
import re
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self
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
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.document_models import (
    DocumentReference,
    DocumentReferences,
    DocumentSnapshot,
    DocumentUpdate,
)
from flowfield.errors import ApplicationError
from flowfield.execution_models import ACTIVE, SettingsEdit
from flowfield.milestone_models import MilestoneBinding, MilestoneUpdate
from flowfield.project_config import Identifier, ProjectConfig, Title, read_config, write_config
from flowfield.publication import (
    DEPENDENCY_CHANGED,
    Publication,
    PublicationStatus,
    Readiness,
    preparation_issue,
    publication_status,
    readiness,
    verification_issue,
)
from flowfield.questions import QuestionReference, Questions, blocking_questions, pending_questions
from flowfield.schema import SCHEMA, SCHEMA_VERSION
from flowfield.storage import acquire_lock, initialize, require_current
from flowfield.work_scopes import WorkScope

Markdown = Annotated[str, StringConstraints(max_length=200_000)]
TaskDescription = Annotated[str, StringConstraints(max_length=400_020)]
ProjectPrefix = Annotated[
    str, StringConstraints(to_upper=True, strip_whitespace=True, pattern=r"^[A-Za-z]{3}$")
]
TaskIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[a-z0-9][a-z0-9_-]{0,63}|(?:[A-Za-z]{3}|[Ww])-[1-9][0-9]*)$"),
]
MilestoneIdentifier = Annotated[
    str, StringConstraints(pattern=r"^(?:[a-z0-9][a-z0-9_-]{0,63}|[Mm]-[1-9][0-9]*)$")
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
    task_prefix: ProjectPrefix | None = None
    coordinator: AgentChoice | None = None
    worker: AgentChoice | None = None
    max_parallel: int = Field(default=1, ge=1, le=16)


class ProjectSetupDefaults(Input):
    # Suggestions may be empty for directory names without a usable identifier.
    # Registration still validates the user's required, editable choices.
    id: str
    name: str
    task_prefix: str


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
        task_prefix=existing.task_prefix if existing else default_task_prefix(identity),
    )


def default_task_prefix(project_id: str) -> str:
    letters = re.sub(r"[^a-z]", "", project_id).upper()
    return (letters + "XXX")[:3] if letters else "PRJ"


class Project(Input):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    id: Identifier
    name: Title
    path: str = Field(min_length=1)
    task_prefix: ProjectPrefix

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
        for field in self.model_fields_set - {"milestone_id"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null; omit it to preserve its value.")
        return self


class ProjectEdit(Edit):
    name: Title | None = None
    description: Markdown | None = None
    task_prefix: ProjectPrefix | None = None
    worker_documents: DocumentReferences | None = None


class WorkCreate(Input):
    title: Title
    id: Identifier | None = None
    body: Markdown = ""
    author: Title = "human"


class MilestoneCreate(WorkCreate):
    acceptance_criteria: Markdown = ""


class MilestoneEdit(Edit):
    title: Title | None = None
    body: Markdown | None = None
    acceptance_criteria: Markdown | None = None


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
    project_id: str


class Milestone(ContentRecord):
    workspace_id: str
    key: str
    acceptance_criteria: Markdown = ""


class TaskPreparation(Input):
    completion: Literal["code", "report"]


class TaskCreate(WorkCreate):
    input_files: list[Identifier] = Field(default_factory=list, max_length=32)
    preparation: TaskPreparation | None = None
    body: TaskDescription = ""
    labels: LabelIds = Field(default_factory=list)
    status: WorkStatus = "backlog"
    milestone_id: MilestoneIdentifier | None = None
    dependencies: list[TaskIdentifier] = Field(default_factory=list)
    documents: DocumentReferences = Field(default_factory=list)

    @field_validator("dependencies", "labels")
    @classmethod
    def normalize_dependencies(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class TaskEdit(Edit):
    input_files: list[Identifier] | None = Field(default=None, max_length=32)
    preparation: TaskPreparation | None = None
    title: Title | None = None
    body: TaskDescription | None = None
    labels: LabelIds | None = None
    milestone_id: MilestoneIdentifier | None = None
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


class TaskProgress(Edit):
    status: WorkStatus
    completion: Literal["report"] | None = None


class TaskReconcile(Edit):
    completion: Literal["code", "report"] | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200_000)]


class TaskReopen(Edit):
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class TaskPublish(Edit):
    completion: Literal["code", "report"]


class TaskRevision(WorkRecord):
    input_files: list[str] = Field(default_factory=list)

    agreement_revision: int = 1
    publication: Publication | None = None
    key: str
    labels: list[str] = Field(default_factory=list)
    status: WorkStatus
    milestone_id: str | None
    archived: bool = False
    dependencies: list[str]
    documents: DocumentReferences = Field(default_factory=list)
    reconciliation_reason: str | None = None
    change_note: str = ""
    report_completion_revision: int | None = None


class TaskReference(BaseModel):
    project_id: str
    id: str
    key: str
    title: str
    status: WorkStatus
    archived: bool
    reconciliation_reason: str | None


class Task(TaskRevision):
    document_updates: list[DocumentUpdate] = Field(default_factory=list)
    milestone_update: MilestoneUpdate | None = None
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
    project: Project
    milestones: list[Milestone]
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
        self, *, write: bool = False, project_id: str | None = None, notify: bool = True
    ) -> Iterator[sqlite3.Connection]:
        with acquire_lock(self.directory, ".initialize.lock", shared=True):
            db = sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            try:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                require_current(db, self.schema_version)
                yield db
                if write and notify and db.total_changes:
                    from flowfield.milestone_verification import reconcile

                    reconcile(db, project_id)
                changed = db.total_changes > 0
                changed_projects = {project_id}
                if (
                    project_id is not None
                    and db.execute(
                        "SELECT 1 FROM sqlite_temp_master WHERE name='changed_task_projects'"
                    ).fetchone()
                ):
                    changed_projects.update(
                        row[0] for row in db.execute("SELECT project_id FROM changed_task_projects")
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
            "updated_by,task_prefix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project.id,
                project.name,
                project.path,
                project.description,
                project.revision,
                project.created_at,
                project.updated_at,
                project.updated_by,
                project.task_prefix,
            ),
        )

    def projects(self) -> list[Project]:
        with self.connection() as db:
            return [
                Project(**dict(row)) for row in db.execute("SELECT * FROM projects ORDER BY id")
            ]

    def _task_prefix(self, db: sqlite3.Connection, project_id: str) -> str:
        candidate = default_task_prefix(project_id)
        used = {row[0] for row in db.execute("SELECT task_prefix FROM projects")}
        # Prefer another word in a related repository name before using a stable
        # alphabetic fallback. Explicit user choices still go through the unique guard.
        candidates = [candidate, *(default_task_prefix(word) for word in project_id.split("-"))]
        for prefix in candidates:
            if prefix not in used:
                return prefix
        for number in range(26**3):
            prefix = "".join(chr(65 + number // divisor % 26) for divisor in (676, 26, 1))
            if prefix not in used:
                return prefix
        raise ApplicationError(
            "prefix_exhausted", "All three-letter task prefixes are in use.", 409
        )

    def _available_prefix(self, db: sqlite3.Connection, prefix: str) -> None:
        if db.execute("SELECT 1 FROM projects WHERE task_prefix=?", (prefix,)).fetchone():
            raise ApplicationError(
                "prefix_taken",
                f"Task prefix {prefix} is already used. Choose another three-letter prefix.",
                409,
            )

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
            if row is None:
                defaults.task_prefix = self._task_prefix(db, defaults.id)
            return defaults

    def setup_project(self, request: ProjectSetup, *, workspace_id: str | None = None) -> Project:
        from flowfield.agent_settings import AgentSettings
        from flowfield.execution import Execution
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
                    if request.coordinator is not None:
                        raise ApplicationError(
                            "workspace_coordinator_settings",
                            "Configure the workspace coordinator separately from project adoption.",
                            409,
                        )
                    if request.worker is not None:
                        raise ApplicationError(
                            "workspace_worker_defaults",
                            "Configure workspace workers separately; "
                            "use individual task overrides for exceptions.",
                            409,
                        )
                    membership = (
                        db.execute(
                            "SELECT workspace_id FROM workspace_scopes WHERE project_id=?",
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
                if existing:
                    coordinator = AgentSettings(self)._read(db, existing.id, "coordinator")
                    workers = Execution(self)._settings(db, existing.id)
                    if (
                        request.coordinator
                        and AgentSettings.normalize(request.coordinator, "coordinator")
                        != coordinator.selection
                    ) or (
                        request.worker
                        and (
                            AgentSettings.normalize(request.worker, "worker") != workers.selection
                            or request.max_parallel != workers.max_parallel
                        )
                    ):
                        raise ApplicationError(
                            "project_exists",
                            "This project is already added. Change its agents in Project settings.",
                            409,
                        )
                    if (
                        request.task_prefix is not None
                        and request.task_prefix != existing.task_prefix
                    ):
                        raise ApplicationError(
                            "project_conflict",
                            "This project already uses a different task prefix. "
                            "Use its saved prefix.",
                            409,
                        )
                    prefix = existing.task_prefix
                elif request.task_prefix is not None:
                    prefix = request.task_prefix
                    self._available_prefix(db, prefix)
                else:
                    prefix = self._task_prefix(db, chosen.project_id)
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
                    task_prefix=prefix,
                    created_at=now(),
                    updated_at=now(),
                    updated_by=request.author,
                )
                self._insert_project(db, project)
                if workspace_id is not None:
                    db.execute(
                        "INSERT INTO workspace_projects VALUES (?,?)", (project.id, workspace_id)
                    )
                # Reuse the settings owners inside registration's transaction.
                if request.coordinator:
                    AgentSettings(self)._edit(
                        db,
                        project.id,
                        "coordinator",
                        AgentSettingsEdit(expected_revision=1, selection=request.coordinator),
                    )
                if request.worker:
                    Execution(self)._configure(
                        db,
                        project.id,
                        SettingsEdit(
                            expected_revision=1,
                            selection=request.worker,
                            max_parallel=request.max_parallel,
                        ),
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

    def _work_scope(self, db: sqlite3.Connection, identity: str) -> WorkScope:
        from flowfield.work_scopes import read

        return read(self, db, identity)

    def work_scope(self, identity: str) -> WorkScope:
        with self.connection() as db:
            return self._work_scope(db, identity)

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
        if project.task_prefix != current.task_prefix:
            if db.execute(
                "SELECT 1 FROM tasks WHERE project_id=? LIMIT 1", (project_id,)
            ).fetchone():
                raise ApplicationError(
                    "prefix_locked",
                    "The prefix is fixed after the first task, including archived tasks, "
                    "to preserve task keys and links.",
                    409,
                )
            self._available_prefix(db, project.task_prefix)
        db.execute(
            "UPDATE projects SET name=?, description=?, revision=?, updated_at=?, "
            "updated_by=?, task_prefix=? WHERE id=?",
            (
                project.name,
                project.description,
                project.revision,
                project.updated_at,
                project.updated_by,
                project.task_prefix,
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
            "SELECT workspace_id FROM workspace_scopes WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            raise ApplicationError(
                "workspace_required", "Add this project to an explicit workspace first.", 409
            )
        return str(row[0])

    def _milestone(self, db: sqlite3.Connection, project_id: str, milestone_id: str) -> Milestone:
        from flowfield.milestones import Milestones

        return Milestones.read(db, self._project_workspace(db, project_id), milestone_id)

    def milestone(self, project_id: str, milestone_id: str) -> Milestone:
        with self.connection() as db:
            return self._milestone(db, project_id, milestone_id)

    def _milestones(self, db: sqlite3.Connection, project_id: str) -> list[Milestone]:
        from flowfield.milestones import Milestones

        return Milestones.read_all(db, self._project_workspace(db, project_id))

    def milestones(self, project_id: str) -> list[Milestone]:
        with self.connection() as db:
            self._project(db, project_id)
            return self._milestones(db, project_id)

    def create_milestone(self, project_id: str, request: MilestoneCreate) -> Milestone:
        from flowfield.milestones import Milestones
        from flowfield.workspaces import Workspaces

        return Milestones(self).create(Workspaces(self).for_project(project_id).id, request)

    def edit_milestone(
        self, project_id: str, milestone_id: str, request: MilestoneEdit
    ) -> Milestone:
        from flowfield.milestones import Milestones
        from flowfield.workspaces import Workspaces

        return Milestones(self).edit(
            Workspaces(self).for_project(project_id).id, milestone_id, request
        )

    def _record_key(self, task: TaskRevision) -> str:
        return task.key

    def _record_rows(
        self, db: sqlite3.Connection, project_id: str, *, metadata: bool = False
    ) -> list[sqlite3.Row]:
        data = "json_set(t.data,'$.body','','$.change_note','')" if metadata else "t.data"
        owner = self._project_workspace(db, project_id)
        return db.execute(
            f"SELECT t.id,t.key,t.position,{data} AS data FROM tasks t "
            "JOIN workspace_scopes p ON p.project_id=t.project_id WHERE p.workspace_id=?",
            (owner,),
        ).fetchall()

    def _records(self, db: sqlite3.Connection, project_id: str) -> dict[str, TaskRevision]:
        return {
            self._record_key(task): task
            for row in self._record_rows(db, project_id, metadata=True)
            for task in [TaskRevision.model_validate_json(row["data"])]
        }

    def _dependency_ids(
        self, db: sqlite3.Connection, project_id: str, references: list[str]
    ) -> list[str]:
        owner = self._project_workspace(db, project_id)
        rows = db.execute(
            "SELECT t.id,t.key,t.project_id FROM tasks t "
            "JOIN workspace_scopes p ON p.project_id=t.project_id WHERE p.workspace_id=?",
            (owner,),
        ).fetchall()
        local = {r["id"]: r["key"] for r in rows if r["project_id"] == project_id}
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
                if prerequisite == key or prerequisite not in records:
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
        completed: set[str] = set()
        for key in self._dependency_order(records):
            task = records[key]
            if (
                task.status == "done"
                and not task.reconciliation_reason
                and publication_status(task) != "needs_reconciliation"
                and all(prerequisite in completed for prerequisite in task.dependencies)
            ):
                completed.add(key)
        return completed

    def _apply_task(self, db: sqlite3.Connection, task: TaskRevision) -> None:
        """Validate and persist the edit and its downstream impacts in one transaction."""
        self._apply_tasks(db, [task])

    def _apply_tasks(self, db: sqlite3.Connection, tasks: list[TaskRevision]) -> None:
        """Apply a checked batch before deriving downstream impacts, once per revision."""
        if not tasks:
            return
        first = tasks[0]
        records = self._records(db, first.project_id)
        changed = {self._record_key(task) for task in tasks}
        for task in tasks:
            if task.input_files:
                from flowfield.task_files import freeze

                freeze(db, self._project_workspace(db, task.project_id), task.input_files)
            if self._record_key(task) not in records:
                raise ApplicationError("invalid_dependency", "Task is outside this workspace.")
            previous_row = db.execute(
                "SELECT data FROM tasks WHERE project_id=? AND id=?",
                (task.project_id, task.id),
            ).fetchone()
            previous = TaskRevision.model_validate_json(previous_row[0]) if previous_row else None
            if previous and (
                previous.body != task.body
                or previous.dependencies != task.dependencies
                or previous.documents != task.documents
                or previous.input_files != task.input_files
            ):
                task.agreement_revision = previous.agreement_revision + 1
                if task.status not in PLANNING:
                    task.reconciliation_reason = (
                        "Requirements changed. Review recorded work with the coordinator."
                    )
                    task.change_note = task.reconciliation_reason
            records[self._record_key(task)] = task
        completed: set[str] = set()
        affected = set(changed)
        for key in self._dependency_order(records):
            item = records[key]
            if any(prerequisite in affected for prerequisite in item.dependencies):
                affected.add(key)
            blocked = [p for p in item.dependencies if p not in completed]
            # A delivered verifier is evidence about frozen inputs. Its milestone
            # record becomes stale; the completed report is not an assignment to
            # rewrite. Explicit changes/reopening still require preparation, and
            # blocked ancestry still prevents downstream work using it as current.
            frozen_report = (
                item.status == "done"
                and item.publication is not None
                and item.publication.completion == "report"
                and item.publication.verification_id is not None
                and item.publication.agreement_revision == item.agreement_revision
            )
            dependency_reason = DEPENDENCY_CHANGED
            if frozen_report and item.reconciliation_reason == dependency_reason:
                # Repair an earlier derived flag through the ordinary revision
                # owner. Retain the prior revision and report; never clear an
                # authored requirement change or mark its ancestry current.
                item = item.model_copy(
                    update={
                        "reconciliation_reason": None,
                        "change_note": (
                            "Retained completed verification; verify changed inputs separately."
                        ),
                        "revision": item.revision + (key not in changed),
                        "updated_at": first.updated_at,
                        "updated_by": first.updated_by,
                    }
                )
                records[key] = item
                changed.add(key)
                affected.add(key)
            if (
                blocked
                and item.status not in PLANNING
                and not item.reconciliation_reason
                and not frozen_report
            ):
                reason = dependency_reason
                if key not in changed:
                    item = TaskRevision.model_validate_json(
                        db.execute(
                            "SELECT data FROM tasks WHERE project_id=? AND id=?",
                            (item.project_id, item.id),
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
                and not blocked
                and not item.reconciliation_reason
                and publication_status(item) != "needs_reconciliation"
            ):
                completed.add(key)
        self._mark_task_projects(db, {records[key].project_id for key in affected})
        for key in changed:
            self._save_task(db, records[key])

    def _task(
        self,
        db: sqlite3.Connection,
        project_id: str,
        task_id: str,
        records: dict[str, TaskRevision] | None = None,
        completed: set[str] | None = None,
    ) -> Task:
        row = db.execute(
            "SELECT * FROM tasks WHERE project_id=? "
            "AND (id=? OR key=? COLLATE NOCASE) ORDER BY id=? DESC",
            (project_id, task_id, task_id, task_id),
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Task not found in this project.", 404)
        current = TaskRevision.model_validate_json(row["data"])
        task_id = current.id
        revisions = [
            TaskRevision.model_validate_json(r[0])
            for r in db.execute(
                "SELECT data FROM task_revisions WHERE project_id=? AND task_id=? "
                "ORDER BY revision",
                (project_id, task_id),
            )
        ]
        records = records if records is not None else self._records(db, project_id)
        completed = completed if completed is not None else self._completed(records, db)
        blocked = [records[p] for p in current.dependencies if p not in completed]
        questions = blocking_questions(db, project_id, task_id)
        publication = publication_status(current)
        from flowfield.document_updates import task_updates
        from flowfield.milestones import milestone_update

        return Task(
            **current.model_dump(),
            position=row["position"],
            milestone_update=milestone_update(db, current),
            document_updates=task_updates(db, project_id, task_id)
            if current.publication and current.publication.documents
            else [],
            latest_update=self._latest_update(db, project_id, task_id),
            revisions=revisions,
            publication_status=publication,
            preparation_issue=preparation_issue(db, current),
            readiness=readiness(
                current,
                publication,
                blocked=bool(blocked or questions or verification_issue(db, current)),
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

    def task(self, project_id: str, task_id: str) -> Task:
        with self.connection() as db:
            return self._task(db, project_id, task_id)

    def _tasks(self, db: sqlite3.Connection, project_id: str, include_archived: bool) -> list[Task]:
        records = self._records(db, project_id)
        completed = self._completed(records, db)
        tasks = [
            self._task(db, project_id, row[0], records, completed)
            for row in db.execute(
                "SELECT id FROM tasks WHERE project_id=? ORDER BY position, id", (project_id,)
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

    def tasks(self, project_id: str, *, include_archived: bool = False) -> list[Task]:
        with self.connection() as db:
            self._project(db, project_id)
            return self._tasks(db, project_id, include_archived)

    def board(self, project_id: str) -> Board:
        with self.connection() as db:
            return Board(
                project=self._project(db, project_id),
                milestones=self._milestones(db, project_id),
                tasks=self._tasks(db, project_id, True),
                **Questions(self).counts(db, project_id),
            )

    @staticmethod
    def _mark_task_projects(db: sqlite3.Connection, projects: set[str]) -> None:
        # Include inferred readiness changes as well as saved revisions. Temporary
        # tracking lets the transaction owner notify affected views only after commit.
        db.execute(
            "CREATE TEMP TABLE IF NOT EXISTS changed_task_projects (project_id TEXT PRIMARY KEY)"
        )
        db.executemany(
            "INSERT OR IGNORE INTO changed_task_projects VALUES (?)",
            [(project,) for project in projects],
        )

    def _save_task(self, db: sqlite3.Connection, task: TaskRevision) -> None:
        self._mark_task_projects(db, {task.project_id})
        previous_row = db.execute(
            "SELECT data FROM task_revisions WHERE project_id=? AND task_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (task.project_id, task.id),
        ).fetchone()
        previous = TaskRevision.model_validate_json(previous_row[0]) if previous_row else None
        messages = []
        if previous is None:
            messages.append(f"Task created in {task.status.replace('_', ' ').capitalize()}.")
        else:
            for field, label in (
                ("body", "Description"),
                ("title", "Title"),
                ("labels", "Labels"),
                ("milestone_id", "Milestone"),
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
                task.project_id,
                task.id,
                uuid4().hex,
                "event",
                "\n\n".join(messages),
                task.updated_by,
                task.updated_at,
                task_revision=task.revision,
            )
        data = task.model_dump_json()
        db.execute(
            "UPDATE tasks SET data=? WHERE project_id=? AND id=?", (data, task.project_id, task.id)
        )
        db.execute(
            "INSERT INTO task_revisions VALUES (?, ?, ?, ?)",
            (task.project_id, task.id, task.revision, data),
        )

    def _append_position(self, db: sqlite3.Connection, project_id: str) -> int:
        return int(
            db.execute(
                "SELECT coalesce(max(t.position),-1)+1 FROM tasks t "
                "JOIN workspace_scopes p USING(project_id) WHERE workspace_id=?",
                (self._project_workspace(db, project_id),),
            ).fetchone()[0]
        )

    def create_task(self, project_id: str, request: TaskCreate) -> Task:
        if request.status == "done":
            raise ApplicationError(
                "completion_required",
                "Create the task, then accept a report or deliver its code before marking Done.",
                409,
            )
        with self.connection(write=True, project_id=project_id) as db:
            project = self._work_scope(db, project_id)
            if request.documents:
                from flowfield.worker_documents import validate_references

                validate_references(db, self._project_workspace(db, project_id), request.documents)
            from flowfield.labels import Labels

            Labels.validate(db, self._project_workspace(db, project_id), request.labels)
            if request.milestone_id is not None:
                request = request.model_copy(
                    update={
                        "milestone_id": self._milestone(db, project_id, request.milestone_id).id
                    }
                )
            number = db.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM tasks WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            task = TaskRevision(
                **request.model_dump(exclude={"id", "author", "dependencies", "preparation"}),
                key=f"{project.task_prefix}-{number}",
                dependencies=self._dependency_ids(db, project_id, request.dependencies),
                id=request.id or generated_id(request.title),
                project_id=project_id,
                revision=1,
                created_at=now(),
                updated_at=now(),
                updated_by=request.author,
            )
            try:
                db.execute(
                    "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        project_id,
                        task.id,
                        self._append_position(db, project_id),
                        task.model_dump_json(),
                        number,
                        task.key,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ApplicationError(
                    "task_exists", "Task ID already exists in this project.", 409
                ) from error
            records = self._records(db, project_id)
            completed = self._completed(records, db)
            if task.status not in PLANNING and any(p not in completed for p in task.dependencies):
                raise ApplicationError(
                    "task_blocked", "Complete prerequisites before recording work.", 409
                )
            self._apply_task(db, task)
            if request.preparation:
                return self._publish_task(
                    db,
                    project_id,
                    task.id,
                    TaskPublish(
                        **request.preparation.model_dump(),
                        expected_revision=task.revision,
                        author=request.author,
                    ),
                )
            return self._task(db, project_id, task.id)

    def edit_task(self, project_id: str, task_id: str, request: TaskEdit) -> Task:
        preparation = request.preparation
        request = TaskEdit.model_validate(
            request.model_dump(exclude_unset=True, exclude={"preparation"})
        )
        with self.connection(write=True, project_id=project_id) as db:
            current = self._task(db, project_id, task_id)
            task_id = current.id
            if request.documents is not None:
                from flowfield.worker_documents import validate_references

                validate_references(db, self._project_workspace(db, project_id), request.documents)
            if request.labels is not None:
                from flowfield.labels import Labels

                Labels.validate(db, self._project_workspace(db, project_id), request.labels)
            if request.dependencies is not None:
                request = request.model_copy(
                    update={
                        "dependencies": self._dependency_ids(db, project_id, request.dependencies)
                    }
                )
            if request.milestone_id is not None:
                request = request.model_copy(
                    update={
                        "milestone_id": self._milestone(db, project_id, request.milestone_id).id
                    }
                )
            values = self._patch(current, request)
            task = TaskRevision(**values)
            task.change_note = ""
            if task.archived and not current.archived:
                issue = self.archive_issue(db, current)
                if issue:
                    raise issue
            if task.revision != current.revision:
                self._apply_task(db, task)
                if current.archived and not task.archived:
                    db.execute(
                        "UPDATE tasks SET position=? WHERE project_id=? AND id=?",
                        (self._append_position(db, project_id), project_id, task_id),
                    )
            if preparation:
                return self._publish_task(
                    db,
                    project_id,
                    task_id,
                    TaskPublish.model_validate(
                        {
                            **preparation.model_dump(),
                            "expected_revision": task.revision,
                            "author": request.author,
                        }
                    ),
                )
            return self._task(db, project_id, task_id)

    def archive_issue(self, db: sqlite3.Connection, task: TaskRevision) -> ApplicationError | None:
        """One archive policy for mutation and browser availability; recheck on write."""
        if task.archived:
            return None
        if pending_questions(db, task.project_id, task.id):
            return ApplicationError(
                "pending_questions", "Resolve pending questions before archiving.", 409
            )
        if db.execute(
            "SELECT 1 FROM runs WHERE project_id=? AND task_id=? AND status IN (?,?,?,?)",
            (task.project_id, task.id, *ACTIVE),
        ).fetchone():
            return ApplicationError(
                "worker_owns_work", "Finish or stop the worker before archiving.", 409
            )
        if db.execute(
            "SELECT 1 FROM task_replies WHERE project_id=? AND task_id=? "
            "AND json_extract(data,'$.status')='pending'",
            (task.project_id, task.id),
        ).fetchone():
            return ApplicationError(
                "reply_pending", "Wait for the pending reply or cancel it before archiving.", 409
            )
        if task.status in ("in_progress", "in_review"):
            return ApplicationError(
                "active_task", "Finish the task or return it to Backlog before archiving.", 409
            )
        return None

    def publish_task(self, project_id: str, task_id: str, request: TaskPublish) -> Task:
        with self.connection(write=True, project_id=project_id) as db:
            return self._publish_task(db, project_id, task_id, request)

    def _publish_task(
        self, db: sqlite3.Connection, project_id: str, task_id: str, request: TaskPublish
    ) -> Task:
        current = self._task(db, project_id, task_id)
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
        documents, milestone = self._prepare_sources(db, current, request.author)
        current = self._task(db, project_id, current.id)
        from flowfield.milestone_delivery import effective_branch, for_milestone

        target_branch = (
            effective_branch(db, project_id, current.milestone_id)
            if request.completion == "code" or for_milestone(db, project_id, current.milestone_id)
            else None
        )
        if request.completion == "code":
            if self._work_scope(db, project_id).project_id is None:
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
            and current.publication.milestone == milestone
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
            milestone=milestone,
            completion=request.completion,
            target_branch=target_branch,
            agreement_revision=current.agreement_revision,
            task_revision=current.revision,
            author=request.author,
            created_at=task.updated_at,
        )
        self._apply_task(db, task)
        return self._task(db, project_id, current.id)

    def _prepare_sources(
        self, db: sqlite3.Connection, current: Task, author: str
    ) -> tuple[list[DocumentSnapshot], MilestoneBinding | None]:
        from flowfield.worker_documents import select

        milestone = (
            self._milestone(db, current.project_id, current.milestone_id)
            if current.milestone_id
            else None
        )
        binding = MilestoneBinding(**milestone.model_dump()) if milestone else None
        documents = select(
            db,
            self._project_workspace(db, current.project_id),
            self._work_scope(db, current.project_id).worker_documents,
            current.documents,
        )
        # A newly adopted revision is a scope change, even if the prose description
        # stayed the same. Old runs/results can never satisfy this newer agreement.
        if (
            current.publication
            and (
                current.publication.documents != documents
                or current.publication.milestone != binding
            )
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
        return documents, binding

    def prioritize_task(self, project_id: str, task_id: str, request: TaskPriority) -> Task:
        with self.connection(write=True, project_id=project_id) as db:
            current = self._task(db, project_id, task_id)
            task_id = current.id
            if request.before_id is not None:
                request = request.model_copy(
                    update={
                        "before_id": self._dependency_ids(db, project_id, [request.before_id])[0]
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
            rows = self._record_rows(db, project_id, metadata=True)
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
                    "report_completion_revision": None,
                    "updated_at": now(),
                    "updated_by": request.author,
                    "change_note": "",
                }
            )
            self._apply_task(db, task)
            # Renumbering neighbors changes order metadata, not their assignments.
            db.executemany(
                "UPDATE tasks SET position=? WHERE project_id=? AND id=?",
                [(i, scope[key].project_id, scope[key].id) for i, key in enumerate(destination)],
            )
            self._mark_task_projects(db, {scope[key].project_id for key in destination})
            return self._task(db, project_id, task_id)

    def record_progress(self, project_id: str, task_id: str, request: TaskProgress) -> Task:
        """Record coordinated work, including reconciliation/reopening; never launch a run."""
        with self.connection(write=True, project_id=project_id) as db:
            current = self._task(db, project_id, task_id)
            task_id = current.id
            if db.execute(
                "SELECT 1 FROM runs WHERE project_id=? AND task_id=?", (project_id, task_id)
            ).fetchone():
                raise ApplicationError(
                    "managed_task",
                    "Use this task's run, stop and review actions; "
                    "manual progress cannot replace managed execution facts.",
                    409,
                )
            self._current(current.revision, request.expected_revision)
            if current.archived:
                raise ApplicationError(
                    "archived_task", "Restore the task before recording progress.", 409
                )
            if request.status not in PLANNING:
                self._require_ready(current)
            if (
                request.status == "done"
                and current.publication
                and current.publication.completion == "code"
            ):
                raise ApplicationError(
                    "delivery_required", "Code work becomes Done only after approved delivery.", 409
                )
            if request.status == "done" and request.completion != "report":
                raise ApplicationError(
                    "completion_required",
                    "Manual completion is only for explicitly accepted reports. "
                    "Code requires approved delivery.",
                    409,
                )
            if current.status == request.status:
                return current
            task = TaskRevision(
                **{
                    **current.model_dump(),
                    "status": request.status,
                    "revision": current.revision + 1,
                    "report_completion_revision": current.revision + 1
                    if request.status == "done"
                    else None,
                    "updated_at": now(),
                    "updated_by": request.author,
                    "change_note": "Recorded work withdrawn for replanning."
                    if current.reconciliation_reason
                    else "",
                    "reconciliation_reason": None,
                }
            )
            self._apply_task(db, task)
            db.execute(
                "UPDATE tasks SET position=? WHERE project_id=? AND id=?",
                (self._append_position(db, project_id), project_id, task_id),
            )
            return self._task(db, project_id, task_id)

    def _require_ready(self, task: Task, *, reconciling: bool = False) -> None:
        if task.blocking_questions:
            raise ApplicationError(
                "task_blocked",
                "Awaiting input application: "
                + ", ".join(q.question for q in task.blocking_questions),
                409,
            )
        if task.blocked_by:
            raise ApplicationError(
                "task_blocked", "Blocked by: " + ", ".join(p.title for p in task.blocked_by), 409
            )
        if task.status in PLANNING and task.publication and task.publication_status == "draft":
            raise ApplicationError(
                "publication_required",
                "The assignment changed. Ask the coordinator to publish it "
                "before recording progress.",
                409,
            )
        if (
            task.reconciliation_reason or task.publication_status == "needs_reconciliation"
        ) and not reconciling:
            raise ApplicationError(
                "reconciliation_required",
                "Review recorded work and reconcile it with an explanation "
                "before recording progress.",
                409,
            )

    def reopen_task(self, project_id: str, task_id: str, request: TaskReopen) -> Task:
        """Prepare a new assignment without rewriting delivered evidence or starting work."""
        with self.connection(write=True, project_id=project_id) as db:
            current = self._task(db, project_id, task_id)
            self._current(current.revision, request.expected_revision)
            if current.archived or current.status != "done":
                raise ApplicationError(
                    "task_not_completed", "Reopen an unarchived completed task for follow-up.", 409
                )
            if (
                db.execute(
                    "SELECT 1 FROM runs WHERE project_id=? AND task_id=? "
                    "AND status IN ('preparing','running','stopping','uncertain')",
                    (project_id, current.id),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM result_versions WHERE project_id=? AND task_id=? "
                    "AND status IN ('preparing','delivering')",
                    (project_id, current.id),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM task_replies WHERE project_id=? AND task_id=? "
                    "AND json_extract(data,'$.status')='pending'",
                    (project_id, current.id),
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
                    "report_completion_revision": None,
                    "updated_at": now(),
                    "updated_by": request.author,
                    "change_note": f"Reopened for follow-up: {request.note}",
                }
            )
            self._apply_task(db, task)
            db.execute(
                "UPDATE tasks SET position=? WHERE project_id=? AND id=?",
                (self._append_position(db, project_id), project_id, current.id),
            )
            return self._task(db, project_id, current.id)

    def _reconcile_verified_dependencies(
        self, db: sqlite3.Connection, tasks: Sequence[tuple[TaskRevision, str]]
    ) -> None:
        """Apply exact dependency-only assessments without adopting new intent or sources."""
        changes = []
        for expected, note in tasks:
            current = self._task(db, expected.project_id, expected.id)
            self._current(current.revision, expected.revision)
            if (
                current.status != "done"
                or current.reconciliation_reason != DEPENDENCY_CHANGED
                or publication_status(current) != "published"
            ):
                raise ApplicationError(
                    "reconciliation_changed",
                    "The dependent assignment changed; review it again.",
                    409,
                )
            changes.append(
                TaskRevision.model_validate(current.model_dump()).model_copy(
                    update={
                        "revision": current.revision + 1,
                        "reconciliation_reason": None,
                        "change_note": note,
                        "updated_at": now(),
                        "updated_by": "flowfield",
                    }
                )
            )
        self._apply_tasks(db, changes)
        # Outside or unresolved ancestry cannot be cleared by a milestone assessment.
        for task in changes:
            if self._task(db, task.project_id, task.id).reconciliation_reason:
                raise ApplicationError(
                    "reconciliation_blocked", "A prerequisite still needs reconciliation.", 409
                )

    def reconcile_task(self, project_id: str, task_id: str, request: TaskReconcile) -> Task:
        with self.connection(write=True, project_id=project_id) as db:
            current = self._task(db, project_id, task_id)
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
            from flowfield.milestone_delivery import effective_branch, for_milestone

            target_branch = (
                effective_branch(db, project_id, current.milestone_id)
                if completion == "code" or for_milestone(db, project_id, current.milestone_id)
                else None
            )
            if completion == "code":
                if not target_branch:
                    raise ApplicationError(
                        "integration_target_required",
                        "Configure the delivery target before reconciling code completion.",
                        409,
                    )
            documents, milestone = self._prepare_sources(db, current, request.author)
            current = self._task(db, project_id, task_id)
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
                        milestone=milestone,
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
            return self._task(db, project_id, task_id)

    def _activity_entry(self, db: sqlite3.Connection, entry_id: str) -> ActivityEntry:
        row = db.execute(ACTIVITY_SELECT + " WHERE a.id=?", (entry_id,)).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Activity entry not found.", 404)
        return ActivityEntry(**dict(row))

    def _insert_activity(
        self,
        db: sqlite3.Connection,
        project_id: str,
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
            "INSERT INTO activity (id, project_id, task_id, kind, body, author, created_at, "
            "supersedes, task_revision, question_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                project_id,
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
        project_id: str,
        task_id: str,
    ) -> ActivityEntry | None:
        row = db.execute(
            ACTIVITY_SELECT + " WHERE a.project_id=? AND a.task_id=? AND a.kind!='event' "
            "ORDER BY a.sequence DESC LIMIT 1",
            (project_id, task_id),
        ).fetchone()
        return ActivityEntry(**dict(row)) if row else None

    def _activity_scope(
        self,
        db: sqlite3.Connection,
        project_id: str,
        task_id: str | None,
    ) -> str | None:
        self._work_scope(db, project_id)
        if task_id is None:
            return None
        row = db.execute(
            "SELECT id FROM tasks WHERE project_id=? "
            "AND (id=? OR key=? COLLATE NOCASE) ORDER BY id=? DESC",
            (project_id, task_id, task_id, task_id),
        ).fetchone()
        if row is None:
            raise ApplicationError("not_found", "Task not found in this project.", 404)
        return str(row[0])

    def activity(
        self,
        project_id: str,
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
            task_id = self._activity_scope(db, project_id, task_id)
            sql = ACTIVITY_SELECT + " WHERE a.project_id=? AND a.task_id IS ?"
            args: list[Any] = [project_id, task_id]
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

    def activity_entry(self, project_id: str, entry_id: str) -> ActivityEntry:
        with self.connection() as db:
            entry = self._activity_entry(db, entry_id)
            if entry.project_id != project_id:
                raise ApplicationError(
                    "not_found", "Activity entry not found in this project.", 404
                )
            return entry

    def add_activity(self, project_id: str, request: ActivityCreate) -> ActivityEntry:
        with self.connection(write=True, project_id=project_id) as db:
            task_id = self._activity_scope(db, project_id, request.task_id)
            # Stable request IDs make a retry after a lost response safe.
            if db.execute("SELECT 1 FROM activity WHERE id=?", (request.id,)).fetchone():
                existing = self._activity_entry(db, request.id)
                if (
                    existing.project_id,
                    existing.task_id,
                    existing.kind,
                    existing.body,
                    existing.author,
                    existing.supersedes,
                    existing.task_revision,
                ) == (
                    project_id,
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
                    "WHERE project_id=? AND id=?",
                    (project_id, task_id),
                ).fetchone()
                latest = db.execute(
                    "SELECT id FROM activity WHERE project_id=? AND task_id=? AND kind='handoff' "
                    "ORDER BY sequence DESC LIMIT 1",
                    (project_id, task_id),
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
                project_id,
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
