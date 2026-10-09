"""Durable decisions with ephemeral delivery authority owned by a live harness turn.

Only the event-loop owner opens turns/requests or answers them. HTTP cannot create
bindings. A saved answer is evidence, never a message to replay after reconnect.
"""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from flowfield.adapters.coordinator_sessions import CoordinatorSessions
from flowfield.agent_models import AgentRole
from flowfield.application import Workspace, now
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.permission_models import (
    PermissionAnswer,
    PermissionOption,
    PermissionPage,
    PermissionRecord,
)


@dataclass
class PermissionTurn:
    owner: "Permissions"
    project: str
    role: AgentRole
    session_id: str
    turn_id: str
    run_id: str | None
    conversation_id: str | None
    task_id: str | None
    generation_id: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    pending: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    open: bool = True

    async def request(
        self,
        tool_id: str,
        title: str,
        options: list[PermissionOption],
        *,
        timeout: float = 300,
        details: str = "",
    ) -> str | None:
        if not self.open or self.pending or not 0 < timeout <= 3600:
            raise ApplicationError("permission_unavailable", "Permission turn is unavailable.", 409)
        if len({option.id for option in options}) != len(options):
            raise ValueError("Permission option IDs must be unique")
        created = now()
        record = PermissionRecord(
            id=uuid4().hex,
            project_id=self.project,
            role=self.role,
            task_id=self.task_id,
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            binding=self.id,
            turn_id=self.turn_id,
            tool_id=tool_id,
            title=title,
            details=details,
            options=options,
            created_at=created,
            updated_at=created,
            expires_at=(datetime.now(UTC) + timedelta(seconds=timeout)).isoformat(),
        )
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.pending[record.id] = future
        try:
            with self.owner.workspace.connection(write=True, project_id=self.project) as db:
                self.owner._live(db, self)
                db.execute(
                    "INSERT INTO agent_permissions(id,project_id,task_id,binding,status,data) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        record.id,
                        self.project,
                        self.task_id,
                        self.id,
                        record.status,
                        record.model_dump_json(),
                    ),
                )
            try:
                answer = await asyncio.wait_for(asyncio.shield(future), timeout)
            except TimeoutError:
                self.owner._end_request(self.project, record.id, "expired")
                return None
            with self.owner.workspace.connection(write=True, project_id=self.project) as db:
                self.owner._live(db, self)
                current = self.owner._get(db, self.project, record.id)
                if current.status != "answered" or current.answer != answer:
                    return None
                current.released_at = now()
                self.owner._save(db, current)
            return answer
        finally:
            self.pending.pop(record.id, None)
            future.cancel()
            self.owner._end_request(self.project, record.id, "cancelled")


class Permissions:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.turns: dict[str, PermissionTurn] = {}

    @staticmethod
    def _get(db: sqlite3.Connection, project: str, identity: str) -> PermissionRecord:
        row = db.execute(
            "SELECT data FROM agent_permissions WHERE project_id=? AND id=?", (project, identity)
        ).fetchone()
        if row is None:
            raise ApplicationError("permission_missing", "Permission request not found.", 404)
        return PermissionRecord.model_validate_json(row[0])

    @staticmethod
    def _save(db: sqlite3.Connection, record: PermissionRecord) -> None:
        record.revision += 1
        record.updated_at = now()
        db.execute(
            "UPDATE agent_permissions SET status=?,data=? WHERE id=?",
            (record.status, record.model_dump_json(), record.id),
        )

    def _live(self, db: sqlite3.Connection, turn: PermissionTurn) -> None:
        if self.turns.get(turn.id) is not turn or not turn.open:
            raise ApplicationError("permission_stale", "The agent turn is no longer waiting.", 409)
        if turn.run_id:
            row = db.execute(
                "SELECT status FROM runs WHERE id=? AND project_id=?", (turn.run_id, turn.project)
            ).fetchone()
            if row is None or row[0] != "running":
                raise ApplicationError("permission_stale", "The worker is no longer running.", 409)
        else:
            if turn.generation_id is None:
                raise ApplicationError(
                    "permission_stale", "The coordinator has no live generation.", 409
                )
            CoordinatorSessions.actor(
                db,
                turn.project,
                turn.turn_id,
                turn.generation_id,
                statuses=("running",),
            )
            generation = CoordinatorSessions.get(db, turn.project, turn.generation_id)
            if generation.session_id != turn.session_id or generation.status != "prompting":
                raise ApplicationError(
                    "permission_stale", "The native permission session changed.", 409
                )

    @asynccontextmanager
    async def turn(
        self,
        project: str,
        role: AgentRole,
        *,
        session_id: str,
        turn_id: str,
        run_id: str | None = None,
        conversation_id: str | None = None,
        generation_id: str | None = None,
    ) -> AsyncIterator[PermissionTurn]:
        if len(self.turns) >= 64 or not session_id or not turn_id:
            raise ApplicationError("permission_capacity", "No permission turn is available.", 409)
        if (role == "worker" and (not run_id or conversation_id)) or (
            role == "coordinator" and (not conversation_id or run_id)
        ):
            raise ValueError("A turn must belong to one worker run or coordinator conversation")
        task_id = None
        with self.workspace.connection() as db:
            self.workspace._project(db, project)
            if run_id:
                row = db.execute(
                    "SELECT task_id,status FROM runs WHERE id=? AND project_id=?", (run_id, project)
                ).fetchone()
                if row is None or row[1] != "running":
                    raise ApplicationError(
                        "permission_stale", "The worker is no longer running.", 409
                    )
                task_id = row[0]
            else:
                CoordinatorStore._conversation(db, project, conversation_id or "")
        if any(
            t.project == project
            and (t.run_id == run_id if run_id else t.conversation_id == conversation_id)
            for t in self.turns.values()
        ):
            raise ApplicationError("permission_busy", "This agent already has a live turn.", 409)
        turn = PermissionTurn(
            self,
            project,
            role,
            session_id,
            turn_id,
            run_id,
            conversation_id,
            task_id,
            generation_id=generation_id,
        )
        self.turns[turn.id] = turn
        try:
            with self.workspace.connection() as db:
                self._live(db, turn)
            yield turn
        finally:
            self._close_turn(turn)

    def _close_turn(self, turn: PermissionTurn) -> None:
        turn.open = False
        self.turns.pop(turn.id, None)
        for identity, future in list(turn.pending.items()):
            self._end_request(turn.project, identity, "cancelled")
            future.cancel()

    def _end_request(
        self, project: str, identity: str, status: Literal["cancelled", "expired"]
    ) -> None:
        with self.workspace.connection(write=True, project_id=project) as db:
            row = db.execute(
                "SELECT data FROM agent_permissions WHERE id=?", (identity,)
            ).fetchone()
            if row:
                current = PermissionRecord.model_validate_json(row[0])
                if current.status == "pending" or (
                    current.status == "answered" and not current.released_at
                ):
                    current.status = status
                    self._save(db, current)

    def restart(self) -> None:
        """Call only after acquiring the service execution lock."""
        if self.turns:
            raise RuntimeError("Cannot recover requests while turns are live")
        with self.workspace.connection(write=True) as db:
            rows = db.execute(
                "SELECT data FROM agent_permissions WHERE status IN "
                "('pending','answered') AND "
                "json_extract(data,'$.released_at') IS NULL"
            ).fetchall()
            for row in rows:
                record = PermissionRecord.model_validate_json(row[0])
                record.status = "cancelled"
                self._save(db, record)

    def close(self) -> None:
        for turn in list(self.turns.values()):
            self._close_turn(turn)

    def close_run(self, project: str, run_id: str) -> None:
        for turn in list(self.turns.values()):
            if turn.project == project and turn.run_id == run_id:
                self._close_turn(turn)

    def answer(self, project: str, identity: str, request: PermissionAnswer) -> PermissionRecord:
        with self.workspace.connection(write=True, project_id=project) as db:
            record = self._get(db, project, identity)
            # Identical HTTP retries are harmless evidence reads, never another delivery.
            if record.status == "answered" and record.answer == request.option_id:
                return record
            turn = self.turns.get(record.binding)
            if turn is None or record.status != "pending":
                raise ApplicationError(
                    "permission_stale", "This request is no longer waiting.", 409
                )
            self._live(db, turn)
            if datetime.fromisoformat(record.expires_at) <= datetime.now(UTC):
                raise ApplicationError(
                    "permission_expired", "This permission request expired.", 409
                )
            future = turn.pending.get(identity)
            if future is None or future.done():
                raise ApplicationError(
                    "permission_stale", "This request is no longer waiting.", 409
                )
            self.workspace._current(record.revision, request.expected_revision)
            if request.option_id not in {option.id for option in record.options}:
                raise ApplicationError("permission_option", "Choose an offered permission option.")
            record.answer = request.option_id
            record.status = "answered"
            self._save(db, record)
        future.set_result(request.option_id)
        return record

    def page(
        self,
        project: str,
        *,
        task_id: str | None = None,
        role: AgentRole | None = None,
        before: int | None = None,
        limit: int = 50,
    ) -> PermissionPage:
        with self.workspace.connection() as db:
            self.workspace._project(db, project)
            if task_id:
                task_id = self.workspace._task(db, project, task_id).id
            pending = db.execute(
                "SELECT data FROM agent_permissions WHERE project_id=? AND status='pending' "
                "AND (? IS NULL OR task_id=?) "
                "AND (? IS NULL OR json_extract(data,'$.role')=?) ORDER BY number",
                (project, task_id, task_id, role, role),
            ).fetchall()
            rows = db.execute(
                "SELECT number,data FROM agent_permissions WHERE project_id=? "
                "AND status!='pending' "
                "AND (? IS NULL OR task_id=?) AND (? IS NULL OR number<?) "
                "AND (? IS NULL OR json_extract(data,'$.role')=?) "
                "ORDER BY number DESC LIMIT ?",
                (project, task_id, task_id, before, before, role, role, limit + 1),
            ).fetchall()
            return PermissionPage(
                pending=[PermissionRecord.model_validate_json(row[0]) for row in pending],
                items=[PermissionRecord.model_validate_json(row[1]) for row in rows[:limit]],
                next_before=rows[limit - 1][0] if len(rows) > limit else None,
            )
