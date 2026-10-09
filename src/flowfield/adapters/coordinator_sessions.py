"""Private native generations; the public conversation remains application-owned."""

import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from flowfield.adapters.harness_host import same_session_location
from flowfield.adapters.session_continuity import retains_session
from flowfield.agent_models import AgentChoice
from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessKind, HarnessLaunch


class NativeGeneration(BaseModel):
    id: str
    project_id: str
    source_id: str | None = None
    created_at: str
    created_by: str | None = None
    harness: HarnessKind
    session_id: str | None = Field(default=None, max_length=2000)
    cwd: str
    launch: HarnessLaunch | None = None
    choice: AgentChoice | None = None
    status: Literal["creating", "created", "prompting", "retained", "failed", "uncertain"]
    dispatched: bool | None = False  # None means an older binding's dispatch is unknown.
    ephemeral: bool = False


class CoordinatorSessions:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    @staticmethod
    def get(db: sqlite3.Connection, project: str, identity: str) -> NativeGeneration:
        row = db.execute(
            "SELECT data FROM coordinator_session_generations WHERE project_id=? AND id=?",
            (project, identity),
        ).fetchone()
        if row is None:
            raise ApplicationError("session_changed", "Native session generation changed.", 409)
        return NativeGeneration.model_validate_json(row[0])

    @staticmethod
    def save(db: sqlite3.Connection, generation: NativeGeneration) -> None:
        db.execute(
            "INSERT INTO coordinator_session_generations VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (generation.id, generation.project_id, generation.model_dump_json()),
        )

    @staticmethod
    def current(db: sqlite3.Connection, project: str) -> tuple[NativeGeneration | None, bool]:
        row = db.execute(
            "SELECT generation_id,fresh FROM coordinator_sessions WHERE project_id=?", (project,)
        ).fetchone()
        return (
            CoordinatorSessions.get(db, project, row[0]) if row and row[0] else None,
            bool(row and row[1]),
        )

    @staticmethod
    def fresh(db: sqlite3.Connection, project: str) -> None:
        # Preserve the old native binding as the source. Clearing a recovery or
        # switching preference must not destroy it or silently resume it on switch-back.
        db.execute(
            "INSERT INTO coordinator_sessions VALUES (?,NULL,1) "
            "ON CONFLICT(project_id) DO UPDATE SET fresh=1",
            (project,),
        )

    @staticmethod
    def location(
        generation: NativeGeneration, harness: str, cwd: str, launch: HarnessLaunch | None
    ) -> None:
        if generation.harness != harness or generation.cwd != cwd:
            raise ApplicationError(
                "agent_resume_failed",
                "The saved session belongs to a different harness or project directory. "
                "Start a new session to continue here.",
                409,
            )
        if launch and (
            (generation.launch and not same_session_location(generation.launch, launch))
            or (
                generation.launch is None
                and "registration" in (launch.executable_source, launch.config_source)
            )
        ):
            raise ApplicationError(
                "agent_resume_failed",
                "The native harness location or host configuration changed. "
                "Start a new session to continue with the saved conversation.",
                409,
            )

    def session(
        self, project: str, harness: str, cwd: str, launch: HarnessLaunch | None = None
    ) -> str | None:
        with self.workspace.connection() as db:
            generation, fresh = self.current(db, project)
            if generation is None or fresh:
                return None
            self.location(generation, harness, cwd, launch)
            return generation.session_id

    @staticmethod
    def actor(
        db: sqlite3.Connection,
        project: str,
        turn: str,
        generation: str,
        *,
        statuses: tuple[str, ...] = ("starting",),
    ) -> None:
        row = db.execute(
            "SELECT status,generation_id FROM coordinator_turns WHERE project_id=? AND id=?",
            (project, turn),
        ).fetchone()
        if row is None or row[0] not in statuses or row[1] != generation:
            raise ApplicationError(
                "session_changed", "The coordinator session generation changed.", 409
            )

    def prepare(
        self,
        db: sqlite3.Connection,
        project: str,
        turn: str,
        choice: AgentChoice,
        cwd: str,
        launch: HarnessLaunch | None,
    ) -> tuple[NativeGeneration, bool]:
        actor = db.execute(
            "SELECT status,generation_id,json_extract(data,'$.native_started') "
            "FROM coordinator_turns WHERE project_id=? AND id=?",
            (project, turn),
        ).fetchone()
        if actor is None or actor[0] != "starting" or actor[2]:
            raise ApplicationError(
                "session_changed", "This coordinator startup is no longer current.", 409
            )
        if actor[1]:
            previous = self.get(db, project, actor[1])
            return previous, previous.created_by != turn
        current, fresh = self.current(db, project)
        resumed = bool(
            current
            and not fresh
            and current.harness == choice.harness
            and (
                retains_session(current.choice, choice)
                or (current.choice is None and choice.harness == "codex")
            )
        )
        if resumed:
            assert current is not None
            self.location(current, choice.harness, cwd, launch)
            generation = current
        else:
            generation = NativeGeneration(
                id=uuid4().hex,
                project_id=project,
                source_id=current.id if current else None,
                created_at=now(),
                created_by=turn,
                harness=choice.harness,
                cwd=cwd,
                launch=launch,
                choice=choice,
                status="creating",
            )
            self.save(db, generation)
        db.execute(
            "UPDATE coordinator_turns SET generation_id=? WHERE project_id=? AND id=?",
            (generation.id, project, turn),
        )
        return generation, resumed

    def created(
        self,
        project: str,
        turn: str,
        generation: str,
        session: str,
        *,
        launch: HarnessLaunch | None = None,
    ) -> None:
        with self.workspace.connection(write=True, project_id=project) as db:
            self.actor(db, project, turn, generation)
            value = self.get(db, project, generation)
            if value.session_id and value.session_id != session:
                raise ApplicationError(
                    "agent_resume_failed", "The harness returned a different saved session.", 409
                )
            if not session or len(session) > 2000:
                raise ApplicationError(
                    "agent_resume_failed", "The harness returned an invalid session identity.", 409
                )
            value.session_id = session
            if value.launch is None:
                value.launch = launch
            value.status = "created"
            self.save(db, value)

    def dispatch(
        self,
        db: sqlite3.Connection,
        project: str,
        turn: str,
        generation: str,
        choice: AgentChoice,
        *,
        command: bool = False,
    ) -> None:
        self.actor(db, project, turn, generation)
        value = self.get(db, project, generation)
        if not value.session_id:
            raise ApplicationError("session_changed", "The native session is not ready.", 409)
        value.choice = choice
        value.status = "prompting"
        value.dispatched = True
        current, _ = self.current(db, project)
        value.ephemeral = command and (current is None or current.id != generation)
        self.save(db, value)
        if not command:
            db.execute(
                "INSERT INTO coordinator_sessions VALUES (?,?,0) "
                "ON CONFLICT(project_id) DO UPDATE SET "
                "generation_id=excluded.generation_id,fresh=0",
                (project, generation),
            )

    def finish(
        self,
        db: sqlite3.Connection,
        project: str,
        turn: str,
        *,
        cleanup_confirmed: bool,
        generation_id: str | None = None,
    ) -> None:
        row = db.execute(
            "SELECT generation_id,status FROM coordinator_turns WHERE project_id=? AND id=?",
            (project, turn),
        ).fetchone()
        if (
            row is None
            or not row[0]
            or row[1] not in ("starting", "running", "stopping", "uncertain")
            or (generation_id is not None and row[0] != generation_id)
        ):
            return
        value = self.get(db, project, row[0])
        current, _ = self.current(db, project)
        value.status = (
            "uncertain"
            if not cleanup_confirmed
            else (
                "retained" if value.session_id and current and current.id == value.id else "failed"
            )
        )
        self.save(db, value)
