"""Service-owned project board operations with guarded storage maintenance."""

import json
import re
import sqlite3
from collections.abc import Callable, Iterator
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
from flowfield.errors import ApplicationError
from flowfield.execution_models import ACTIVE, SettingsEdit
from flowfield.execution_models import SCHEMA as EXECUTION_SCHEMA
from flowfield.inspection_models import SCHEMA as INSPECTION_SCHEMA
from flowfield.integration_models import SCHEMA as INTEGRATION_SCHEMA
from flowfield.migrations import current_version
from flowfield.project_config import Identifier, ProjectConfig, Title, read_config, write_config
from flowfield.publication import (
    Publication,
    PublicationStatus,
    Readiness,
    preparation_issue,
    publication_status,
    readiness,
    stages_need_reconciliation,
)
from flowfield.questions import QuestionReference, Questions, blocking_questions, pending_questions
from flowfield.reply_models import SCHEMA as REPLY_SCHEMA
from flowfield.result_models import SCHEMA as RESULT_SCHEMA
from flowfield.run_activity import SCHEMA as ACTIVITY_SCHEMA
from flowfield.search import SCHEMA as SEARCH_SCHEMA
from flowfield.stage_models import SCHEMA as STAGE_SCHEMA
from flowfield.stage_models import Stage, StageChange, StagePlan, StageUpdate
from flowfield.storage import acquire_lock, initialize, require_current

Markdown = Annotated[str, StringConstraints(max_length=200_000)]
TaskDescription = Annotated[str, StringConstraints(max_length=400_020)]
ProjectPrefix = Annotated[
    str, StringConstraints(to_upper=True, strip_whitespace=True, pattern=r"^[A-Za-z]{3}$")
]
TaskIdentifier = Annotated[
    str, StringConstraints(pattern=r"^(?:[a-z0-9][a-z0-9_-]{0,63}|[A-Za-z]{3}-[1-9][0-9]*)$")
]
MilestoneIdentifier = Annotated[
    str, StringConstraints(pattern=r"^(?:[a-z0-9][a-z0-9_-]{0,63}|[Mm]-[1-9][0-9]*)$")
]
WorkStatus = Literal["backlog", "up_next", "in_progress", "in_review", "done"]
PlanningStatus = Literal["backlog", "up_next"]
PLANNING = ("backlog", "up_next")
TaskType = Literal["feature", "bug", "maintenance", "investigation"]
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


class WorkCreate(Input):
    title: Title
    id: Identifier | None = None
    body: Markdown = ""
    author: Title = "human"


class MilestoneCreate(WorkCreate):
    pass


class MilestoneEdit(Edit):
    title: Title | None = None
    body: Markdown | None = None


class WorkRecord(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    project_id: str
    id: str
    title: str
    body: str
    revision: int
    created_at: str
    updated_at: str
    updated_by: str


class Milestone(WorkRecord):
    key: str


class TaskPreparation(Input):
    completion: Literal["code", "report"]


class TaskCreate(WorkCreate):
    stages: list[Stage] = Field(min_length=1, max_length=8)

    @field_validator("stages")
    @classmethod
    def valid_stages(cls, value: list[Stage]) -> list[Stage]:
        return StageChange(expected_revision=0, stages=value, reason="Initial plan").stages

    preparation: TaskPreparation | None = None
    body: TaskDescription = ""
    task_type: TaskType = "feature"
    status: WorkStatus = "backlog"
    milestone_id: MilestoneIdentifier | None = None
    dependencies: list[TaskIdentifier] = Field(default_factory=list)

    @field_validator("dependencies")
    @classmethod
    def normalize_dependencies(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class TaskEdit(Edit):
    stages: StageChange | None = None
    preparation: TaskPreparation | None = None
    title: Title | None = None
    body: TaskDescription | None = None
    task_type: TaskType | None = None
    milestone_id: MilestoneIdentifier | None = None
    archived: bool | None = None
    dependencies: list[TaskIdentifier] | None = None

    @field_validator("dependencies")
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


class TaskPublish(Edit):
    stages: StageChange | None = None
    completion: Literal["code", "report"]


class TaskRevision(WorkRecord):
    agreement_revision: int = 1
    publication: Publication | None = None
    key: str
    task_type: TaskType
    status: WorkStatus
    milestone_id: str | None
    archived: bool = False
    dependencies: list[str]
    reconciliation_reason: str | None = None
    change_note: str = ""
    report_completion_revision: int | None = None


class TaskReference(BaseModel):
    id: str
    key: str
    title: str
    status: WorkStatus
    archived: bool
    reconciliation_reason: str | None


class Task(TaskRevision):
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


# Initialization baseline for schema 44. New database changes belong in migrations.py.
SCHEMA = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL, revision INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
    task_prefix TEXT NOT NULL UNIQUE CHECK(task_prefix GLOB '[A-Z][A-Z][A-Z]')
);
CREATE TABLE milestones (
    project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL, data TEXT NOT NULL,
    number INTEGER NOT NULL, key TEXT NOT NULL,
    PRIMARY KEY (project_id, id), UNIQUE(project_id, number), UNIQUE(project_id, key)
);
CREATE TABLE tasks (
    project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
    position INTEGER NOT NULL, data TEXT NOT NULL, number INTEGER NOT NULL,
    key TEXT NOT NULL UNIQUE, PRIMARY KEY (project_id, id), UNIQUE(project_id, number)
);
CREATE TABLE task_revisions (
    project_id TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id, revision),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE TABLE activity (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('note', 'handoff', 'event')),
    body TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL,
    supersedes TEXT UNIQUE REFERENCES activity(id), task_revision INTEGER, question_id TEXT,
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE INDEX activity_scope ON activity(project_id, task_id, sequence);
CREATE TABLE questions (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
    task_id TEXT, status TEXT NOT NULL, data TEXT NOT NULL,
    UNIQUE(project_id, id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE INDEX question_scope ON questions(project_id, task_id, status);
CREATE TABLE question_revisions (
    project_id TEXT NOT NULL, question_id TEXT NOT NULL,
    revision INTEGER NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY(project_id, question_id, revision),
    FOREIGN KEY (project_id, question_id) REFERENCES questions(project_id, id)
);
PRAGMA user_version = 44;
"""
SCHEMA += EXECUTION_SCHEMA + INTEGRATION_SCHEMA

SCHEMA += RESULT_SCHEMA + SEARCH_SCHEMA + INSPECTION_SCHEMA + STAGE_SCHEMA
SCHEMA += REPLY_SCHEMA
SCHEMA += ACTIVITY_SCHEMA
SCHEMA += (
    "\n"
    "CREATE TABLE storage_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), workspace_id "
    "TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision >= 0));\n"
    "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL,"
    " app_version TEXT NOT NULL, backup TEXT);\n"
    "CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL "
    "UNIQUE, source TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, "
    "dismissed_at TEXT, resolved_at TEXT);\n"
    "CREATE TABLE notification_deliveries (notification_id INTEGER NOT NULL REFERENCES "
    "notifications(id) ON DELETE CASCADE, channel TEXT NOT NULL, claimed_at TEXT NOT NULL, "
    "PRIMARY KEY(notification_id, channel));\n"
    "CREATE TABLE notification_state (name TEXT PRIMARY KEY, data TEXT NOT NULL);\n"
    "CREATE TABLE agent_settings (project_id TEXT NOT NULL REFERENCES projects(id), role "
    "TEXT NOT NULL CHECK(role IN ('worker','coordinator')), scope TEXT NOT NULL, revision "
    "INTEGER NOT NULL, selection TEXT, PRIMARY KEY(project_id,role,scope));\n"
    "CREATE TABLE agent_permissions (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT "
    "NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT, binding "
    "TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, FOREIGN "
    "KEY(project_id,task_id) REFERENCES tasks(project_id,id));\n"
    "CREATE INDEX permissions_project ON agent_permissions(project_id,task_id,number);\n"
    "CREATE INDEX permissions_binding ON agent_permissions(binding,status);\n"
    "CREATE TABLE coordinator_conversations (number INTEGER PRIMARY KEY AUTOINCREMENT, id "
    "TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), created_at "
    "TEXT NOT NULL);\n"
    "CREATE TABLE coordinator_turns (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT "
    "NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), conversation_id TEXT "
    "NOT NULL REFERENCES coordinator_conversations(id), status TEXT NOT NULL, data TEXT NOT"
    " NULL);\n"
    "CREATE INDEX coordinator_projects ON coordinator_conversations(project_id,number);\n"
    "CREATE INDEX coordinator_history ON coordinator_turns(conversation_id,number);\n"
    "CREATE UNIQUE INDEX coordinator_active ON coordinator_turns(project_id) WHERE status "
    "IN ('starting','running','stopping','uncertain');\n"
    "CREATE INDEX coordinator_project_messages ON coordinator_turns(project_id,number);\n"
    "CREATE TABLE attachments (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES "
    "projects(id), task_id TEXT, name TEXT NOT NULL, mime TEXT NOT NULL, created_at TEXT "
    "NOT NULL, content BLOB NOT NULL, size INTEGER NOT NULL, bound INTEGER NOT NULL DEFAULT"
    " 0, FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id));\n"
    "CREATE INDEX attachment_project ON attachments(project_id,task_id);\n"
    "CREATE TABLE coordinator_sessions (project_id TEXT PRIMARY KEY REFERENCES "
    "projects(id), harness TEXT NOT NULL, session_id TEXT NOT NULL, cwd TEXT NOT NULL);\n"
    "INSERT INTO storage_metadata VALUES (1, lower(hex(randomblob(16))), 0);\n"
)


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
    ):
        self.on_activity = on_activity
        self.on_change = on_change
        self.directory = directory.expanduser().resolve()
        self.database = self.directory / "workspace.sqlite3"
        self.schema_version = current_version()
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
                changed = db.total_changes > 0
                if changed:
                    db.execute("UPDATE storage_metadata SET revision=revision+1 WHERE id=1")
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
        if write and changed and notify and self.on_change is not None:
            self.on_change(project_id)

    def _insert_project(self, db: sqlite3.Connection, project: Project) -> None:
        db.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        self._available_prefix(db, candidate)
        return candidate

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
            return setup_defaults(path, config, Project(**dict(row)) if row else None)

    def setup_project(self, request: ProjectSetup) -> Project:
        from flowfield.agent_settings import AgentSettings
        from flowfield.execution import Execution

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
        return project

    def _milestone(self, db: sqlite3.Connection, project_id: str, milestone_id: str) -> Milestone:
        row = db.execute(
            "SELECT data FROM milestones WHERE project_id=? "
            "AND (id=? OR key=? COLLATE NOCASE) ORDER BY id=? DESC",
            (project_id, milestone_id, milestone_id, milestone_id),
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Milestone not found in this project.", 404)
        return Milestone.model_validate_json(row[0])

    def milestone(self, project_id: str, milestone_id: str) -> Milestone:
        with self.connection() as db:
            return self._milestone(db, project_id, milestone_id)

    def _milestones(self, db: sqlite3.Connection, project_id: str) -> list[Milestone]:
        return [
            Milestone.model_validate_json(row[0])
            for row in db.execute(
                "SELECT data FROM milestones WHERE project_id=? ORDER BY number", (project_id,)
            )
        ]

    def milestones(self, project_id: str) -> list[Milestone]:
        with self.connection() as db:
            self._project(db, project_id)
            return self._milestones(db, project_id)

    def create_milestone(self, project_id: str, request: MilestoneCreate) -> Milestone:
        with self.connection(write=True, project_id=project_id) as db:
            self._project(db, project_id)
            number = db.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM milestones WHERE project_id=?",
                (project_id,),
            ).fetchone()[0]
            milestone = Milestone(
                project_id=project_id,
                id=request.id or generated_id(request.title),
                key=f"M-{number}",
                title=request.title,
                body=request.body,
                revision=1,
                created_at=now(),
                updated_at=now(),
                updated_by=request.author,
            )
            try:
                db.execute(
                    "INSERT INTO milestones VALUES (?, ?, ?, ?, ?)",
                    (project_id, milestone.id, milestone.model_dump_json(), number, milestone.key),
                )
            except sqlite3.IntegrityError as error:
                raise ApplicationError(
                    "milestone_exists", "Milestone ID already exists.", 409
                ) from error
            return milestone

    def edit_milestone(
        self, project_id: str, milestone_id: str, request: MilestoneEdit
    ) -> Milestone:
        with self.connection(write=True, project_id=project_id) as db:
            milestone = Milestone(
                **self._patch(self._milestone(db, project_id, milestone_id), request)
            )
            db.execute(
                "UPDATE milestones SET data=? WHERE project_id=? AND id=?",
                (milestone.model_dump_json(), project_id, milestone.id),
            )
            return milestone

    def _records(self, db: sqlite3.Connection, project_id: str) -> dict[str, TaskRevision]:
        return {
            row["id"]: TaskRevision.model_validate_json(row["data"])
            for row in db.execute("SELECT id, data FROM tasks WHERE project_id=?", (project_id,))
        }

    def _dependency_ids(
        self, db: sqlite3.Connection, project_id: str, references: list[str]
    ) -> list[str]:
        # Keep relationships canonical; short keys and internal IDs address the same task.
        keys = {
            row["key"]: row["id"]
            for row in db.execute("SELECT id, key FROM tasks WHERE project_id=?", (project_id,))
        }
        identities = set(keys.values())
        return sorted(
            {
                reference if reference in identities else keys.get(reference.upper(), reference)
                for reference in references
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
                        "Choose another task in this project as a prerequisite.",
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
        changed = {task.id for task in tasks}
        for task in tasks:
            previous = records.get(task.id)
            if previous and (
                previous.body != task.body or previous.dependencies != task.dependencies
            ):
                task.agreement_revision = previous.agreement_revision + 1
                if task.status not in PLANNING:
                    task.reconciliation_reason = (
                        "Requirements changed. Review recorded work with the coordinator."
                    )
                    task.change_note = task.reconciliation_reason
            records[task.id] = task
        completed: set[str] = set()
        for key in self._dependency_order(records):
            item = records[key]
            blocked = [p for p in item.dependencies if p not in completed]
            if blocked and item.status not in PLANNING and not item.reconciliation_reason:
                reason = "Prerequisites changed. Review recorded work with the coordinator."
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
        return Task(
            **current.model_dump(),
            position=row["position"],
            latest_update=self._latest_update(db, project_id, task_id),
            revisions=revisions,
            publication_status=publication,
            preparation_issue=preparation_issue(db, current),
            readiness=readiness(
                current,
                publication,
                blocked=bool(blocked or questions),
                stages_stale=stages_need_reconciliation(
                    db, project_id, task_id, current.agreement_revision
                ),
            ),
            blocked_by=[TaskReference(**item.model_dump()) for item in blocked],
            blocking_questions=questions,
            prerequisites=[TaskReference(**records[p].model_dump()) for p in current.dependencies],
            dependents=[
                TaskReference(**item.model_dump())
                for item in records.values()
                if task_id in item.dependencies
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

    def _save_task(self, db: sqlite3.Connection, task: TaskRevision) -> None:
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
                ("task_type", "Type"),
                ("milestone_id", "Milestone"),
                ("dependencies", "Prerequisites"),
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
                "SELECT COALESCE(MAX(position), -1) + 1 FROM tasks WHERE project_id=?",
                (project_id,),
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
            project = self._project(db, project_id)
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
                **request.model_dump(
                    exclude={"id", "author", "dependencies", "preparation", "stages"}
                ),
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
            initial_plan = StagePlan(
                project_id=project_id,
                task_id=task.id,
                revision=1,
                agreement_revision=task.agreement_revision,
                stages=request.stages,
                reason="Initial task plan",
                author=request.author,
                created_at=now(),
            )
            db.execute(
                "INSERT INTO stage_plans VALUES (?,?,?,?)",
                (project_id, task.id, 1, initial_plan.model_dump_json()),
            )
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
        stage_change = request.stages
        request = TaskEdit.model_validate(
            request.model_dump(exclude_unset=True, exclude={"preparation", "stages"})
        )
        with self.connection(write=True, project_id=project_id) as db:
            current = self._task(db, project_id, task_id)
            task_id = current.id
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
            if stage_change:
                self._change_task_stages(db, task, stage_change)
            if preparation:
                return self._publish_task(
                    db,
                    project_id,
                    task_id,
                    TaskPublish(
                        **preparation.model_dump(),
                        expected_revision=task.revision,
                        author=request.author,
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

    def _change_task_stages(
        self, db: sqlite3.Connection, task: TaskRevision, change: StageChange
    ) -> None:
        from flowfield.stages import Stages

        Stages(self)._update(
            db,
            task.project_id,
            task.id,
            StageUpdate(**change.model_dump(), agreement_revision=task.agreement_revision),
        )

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
        if request.stages:
            self._change_task_stages(db, current, request.stages)
            current = self._task(db, project_id, current.id)
        from flowfield.stages import Stages

        plan = Stages(self)._get(db, current)
        if not plan.stages or plan.agreement_revision != current.agreement_revision:
            raise ApplicationError(
                "stages_required",
                "Provide current stages with this preparation; at least one stage is required.",
                409,
            )
        target_branch = None
        if request.completion == "code":
            settings = db.execute(
                "SELECT data FROM integration_settings WHERE project_id=?", (project_id,)
            ).fetchone()
            target_branch = json.loads(settings[0])["target_branch"] if settings else None
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
            completion=request.completion,
            target_branch=target_branch,
            agreement_revision=current.agreement_revision,
            task_revision=current.revision,
            author=request.author,
            created_at=task.updated_at,
        )
        self._apply_task(db, task)
        return self._task(db, project_id, current.id)

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
            destination = [
                t.id
                for t in self._tasks(db, project_id, False)
                if t.status == request.status and t.id != task_id
            ]
            if request.before_id is not None and request.before_id not in destination:
                raise ApplicationError(
                    "invalid_order", "Choose another active task in the destination column."
                )
            index = destination.index(request.before_id) if request.before_id else len(destination)
            destination.insert(index, task_id)
            previous = [
                t.id for t in self._tasks(db, project_id, False) if t.status == request.status
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
                [(i, project_id, identity) for i, identity in enumerate(destination)],
            )
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
            target_branch = current.publication.target_branch if current.publication else None
            if request.completion == "code":
                settings = db.execute(
                    "SELECT data FROM integration_settings WHERE project_id=?", (project_id,)
                ).fetchone()
                target_branch = json.loads(settings[0])["target_branch"] if settings else None
                if not target_branch:
                    raise ApplicationError(
                        "integration_target_required",
                        "Configure the delivery target before reconciling code completion.",
                        409,
                    )
            elif request.completion == "report":
                target_branch = None
            task = TaskRevision(
                **{
                    **current.model_dump(),
                    "revision": current.revision + 1,
                    "updated_at": now(),
                    "updated_by": request.author,
                    "reconciliation_reason": None,
                    "change_note": request.note,
                    "publication": Publication(
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
        self._project(db, project_id)
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
