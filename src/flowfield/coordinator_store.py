"""Transactional chat ownership, bounded public output and retained history."""

import json
import sqlite3
from typing import Any
from uuid import uuid4

from flowfield.activity_text import preview
from flowfield.adapters.coordinator_sessions import CoordinatorSessions
from flowfield.agent_settings import AgentSettings
from flowfield.application import Workspace, now
from flowfield.attachments import Attachments
from flowfield.coordinator_handoff import freeze
from flowfield.coordinator_models import (
    CoordinatorConversation,
    CoordinatorPage,
    CoordinatorSend,
    CoordinatorTaskContext,
    CoordinatorTurn,
)
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessLaunch
from flowfield.results import Results
from flowfield.run_activity import ActivityUpdate, ContextUsage, update_activity


class CoordinatorStore:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.sessions = CoordinatorSessions(workspace)

    def session(
        self, project: str, harness: str, cwd: str, *, launch: HarnessLaunch | None = None
    ) -> str | None:
        return self.sessions.session(project, harness, cwd, launch)

    @staticmethod
    def _recovery(db: sqlite3.Connection, project: str) -> str | None:
        row = db.execute(
            "SELECT t.id FROM coordinator_turns t JOIN coordinator_sessions s "
            "ON s.project_id=t.project_id WHERE t.project_id=? "
            "AND t.number=(SELECT MAX(number) FROM coordinator_turns WHERE project_id=?) "
            "AND json_extract(t.data,'$.session')='unavailable' "
            "AND s.generation_id IS NOT NULL AND s.fresh=0",
            (project, project),
        ).fetchone()
        return row[0] if row and not CoordinatorStore._active(db, project) else None

    def reset_session(self, project: str, failed_turn: str) -> None:
        with self.workspace.connection(write=True, project_id=project) as db:
            if self._recovery(db, project) != failed_turn:
                raise ApplicationError(
                    "session_changed", "Session recovery changed. Refresh before continuing.", 409
                )
            self.sessions.fresh(db, project)
            turn = self._get(db, project, failed_turn)
            turn.notice += " New agent session selected. Your chat is saved."
            self._save(db, turn)

    @staticmethod
    def _conversation(
        db: sqlite3.Connection, project: str, identity: str
    ) -> CoordinatorConversation:
        row = db.execute(
            "SELECT id,number,created_at FROM coordinator_conversations "
            "WHERE project_id=? AND id=?",
            (project, identity),
        ).fetchone()
        if row is None:
            raise ApplicationError("conversation_missing", "Conversation not found.", 404)
        return CoordinatorConversation(id=row[0], number=row[1], created_at=row[2])

    @staticmethod
    def _get(db: sqlite3.Connection, project: str, identity: str) -> CoordinatorTurn:
        row = db.execute(
            "SELECT data FROM coordinator_turns WHERE project_id=? AND id=?", (project, identity)
        ).fetchone()
        if row is None:
            raise ApplicationError("turn_missing", "Coordinator turn not found.", 404)
        return CoordinatorTurn.model_validate_json(row[0])

    @staticmethod
    def _save(db: sqlite3.Connection, turn: CoordinatorTurn) -> None:
        db.execute(
            "UPDATE coordinator_turns SET status=?,data=? WHERE id=?",
            (turn.status, turn.model_dump_json(), turn.id),
        )

    @staticmethod
    def _active(db: sqlite3.Connection, project: str) -> CoordinatorTurn | None:
        row = db.execute(
            "SELECT data FROM coordinator_turns WHERE project_id=? "
            "AND status IN ('starting','running','stopping','uncertain')",
            (project,),
        ).fetchone()
        return CoordinatorTurn.model_validate_json(row[0]) if row else None

    def new(self, project: str) -> CoordinatorConversation:
        with self.workspace.connection(write=True, project_id=project) as db:
            self.workspace._project(db, project)
            existing = db.execute(
                "SELECT id FROM coordinator_conversations WHERE project_id=? "
                "ORDER BY number DESC LIMIT 1",
                (project,),
            ).fetchone()
            if existing:
                return self._conversation(db, project, existing[0])
            identity = uuid4().hex
            db.execute(
                "INSERT INTO coordinator_conversations(id,project_id,created_at) VALUES (?,?,?)",
                (identity, project, now()),
            )
            return self._conversation(db, project, identity)

    def page(
        self,
        project: str,
        conversation: str | None = None,
        before: int | None = None,
        *,
        after: int | None = None,
    ) -> CoordinatorPage:
        if after is not None and before is not None:
            raise ApplicationError("invalid_cursor", "Choose either earlier or newer messages.")
        with self.workspace.connection() as db:
            self.workspace._project(db, project)
            owner = self._conversation(db, project, conversation) if conversation else None
            if after is not None:
                rows = db.execute(
                    "SELECT data FROM coordinator_turns WHERE project_id=? AND number>=? "
                    "ORDER BY number LIMIT 20",
                    (project, after),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT data FROM coordinator_turns WHERE project_id=? "
                    "AND (? IS NULL OR number<?) ORDER BY number DESC LIMIT 21",
                    (project, before, before),
                ).fetchall()
            items = [CoordinatorTurn.model_validate_json(row[0]) for row in rows[:20]]
            for turn in items:
                for entry in turn.activity.items:
                    entry.preview = preview(entry.text, entry.kind)
                    entry.abridged = entry.preview != entry.text
            # Commands and failed/starting turns may not emit usage. Read the last
            # report independently of the visible history window, across restarts,
            # but never carry it across an explicitly started replacement session.
            context = db.execute(
                "SELECT json_extract(data,'$.activity.context') FROM coordinator_turns "
                "WHERE project_id=? AND (json_type(data,'$.activity.context')='object' "
                "OR json_extract(data,'$.session')='new') "
                "ORDER BY number DESC LIMIT 1",
                (project,),
            ).fetchone()
            return CoordinatorPage(
                context=ContextUsage.model_validate_json(context[0])
                if context and context[0]
                else None,
                conversation=owner,
                items=items if after is not None else list(reversed(items)),
                next_before=items[-1].number if len(rows) > 20 else None,
                active=self._active(db, project),
                session_recovery_turn_id=self._recovery(db, project),
            )

    def reserve(
        self, project: str, conversation: str, request: CoordinatorSend, *, available: bool = True
    ) -> tuple[CoordinatorTurn, bool]:
        with self.workspace.connection(write=True, project_id=project) as db:
            self._conversation(db, project, conversation)
            task = (
                self.workspace._task(db, project, request.task_context.task_id)
                if request.task_context
                else None
            )
            row = db.execute(
                "SELECT project_id FROM coordinator_turns WHERE id=?", (request.id,)
            ).fetchone()
            if row:
                if row[0] != project:
                    raise ApplicationError(
                        "message_conflict", "Message identity is already used.", 409
                    )
                old = self._get(db, project, request.id)
                same_context = (old.task_context is None and request.task_context is None) or (
                    old.task_context is not None
                    and request.task_context is not None
                    and task is not None
                    and old.task_context.task_id == task.id
                    and old.task_context.task_revision == request.task_context.task_revision
                    and old.task_context.result_id == request.task_context.result_id
                )
                if (
                    old.text != request.text
                    or old.conversation_id != conversation
                    or not same_context
                ):
                    raise ApplicationError(
                        "message_conflict", "This message identity has different content.", 409
                    )
                return old, False
            if (
                task
                and request.task_context
                and task.revision != request.task_context.task_revision
            ):
                raise ApplicationError(
                    "task_context_changed",
                    "The selected task changed. Review it before sending again.",
                    409,
                )
            if task and request.task_context and request.task_context.result_id:
                selected_result = Results(self.workspace)._get(
                    db, project, request.task_context.result_id
                )
                if selected_result.task_id != task.id:
                    raise ApplicationError(
                        "context_result_missing",
                        "Result does not belong to the selected task.",
                        404,
                    )
            if not available:
                raise ApplicationError(
                    "coordinator_capacity",
                    "All coordinator slots are busy. Try again shortly.",
                    409,
                )
            if self._active(db, project):
                raise ApplicationError(
                    "coordinator_busy", "The coordinator already has an active turn.", 409
                )
            if (
                db.execute(
                    "SELECT count(*) FROM coordinator_turns "
                    "WHERE status IN ('starting','running','stopping','uncertain')"
                ).fetchone()[0]
                >= 16
            ):
                raise ApplicationError(
                    "coordinator_capacity",
                    "Coordinator capacity is reserved by active or "
                    "unreconciled turns. Stop running work or confirm interrupted cleanup first.",
                    409,
                )
            if not request.text.strip():
                raise ApplicationError("empty_message", "Write a message first.")
            settings = AgentSettings(self.workspace).resolve(db, project, "coordinator").effective
            if settings is None:
                raise ApplicationError(
                    "coordinator_settings",
                    "Choose a coordinator model and effort in settings first.",
                    409,
                )
            settings = AgentSettings(self.workspace).freeze(db, settings)
            turn = CoordinatorTurn(
                task_context=CoordinatorTaskContext(
                    task_id=task.id,
                    task_revision=task.revision,
                    key=task.key,
                    title=task.title,
                    result_id=request.task_context.result_id if request.task_context else None,
                )
                if task
                else None,
                id=request.id,
                project_id=project,
                conversation_id=conversation,
                text=request.text,
                settings=settings,
                created_at=now(),
            )
            Attachments(self.workspace).references(db, project, None, request.text, bind=True)
            result = db.execute(
                "INSERT INTO coordinator_turns(id,project_id,conversation_id,status,data) "
                "VALUES (?,?,?,?,?)",
                (turn.id, project, conversation, turn.status, turn.model_dump_json()),
            )
            turn.number = result.lastrowid or 0
            self._save(db, turn)
            db.execute(
                "INSERT INTO coordinator_handoffs VALUES (?,?)",
                (turn.id, json.dumps(freeze(db, turn), ensure_ascii=False)),
            )
            return turn, True

    def handoff(self, project: str, identity: str) -> dict[str, Any]:
        with self.workspace.connection() as db:
            self._get(db, project, identity)
            row = db.execute(
                "SELECT data FROM coordinator_handoffs WHERE turn_id=?", (identity,)
            ).fetchone()
            if row is None:
                raise ApplicationError(
                    "handoff_unavailable",
                    "This older message has no frozen handoff. "
                    "Send a new message to continue; interrupted prompts are not replayed.",
                    409,
                )
            value: dict[str, Any] = json.loads(row[0])
            return value

    def get(self, project: str, identity: str) -> CoordinatorTurn:
        with self.workspace.connection() as db:
            return self._get(db, project, identity)

    def write(
        self,
        project: str,
        identity: str,
        updates: list[ActivityUpdate],
        *,
        generation_id: str | None = None,
    ) -> None:
        with self.workspace.connection(write=True, notify=False) as db:
            turn = self._get(db, project, identity)
            if turn.status not in ("starting", "running", "stopping"):
                return
            if generation_id is not None:
                row = db.execute(
                    "SELECT generation_id FROM coordinator_turns WHERE project_id=? AND id=?",
                    (project, identity),
                ).fetchone()
                if row is None or row[0] != generation_id:
                    return
            update_activity(turn.activity, updates)
            self._save(db, turn)

        if self.workspace.on_activity:
            self.workspace.on_activity(project, identity)

    def restart(self) -> None:
        with self.workspace.connection(write=True) as db:
            rows = db.execute(
                "SELECT data FROM coordinator_turns "
                "WHERE status IN ('starting','running','stopping')"
            ).fetchall()
            for row in rows:
                turn = CoordinatorTurn.model_validate_json(row[0])
                self.sessions.finish(db, turn.project_id, turn.id, cleanup_confirmed=False)
                turn.status = "uncertain" if turn.native_started else "interrupted"
                turn.notice = "The service restarted during this turn. Nothing was replayed. " + (
                    "Stop any remaining coordinator process on the host, then confirm it stopped."
                    if turn.native_started
                    else "Send a new message to continue."
                )
                self._save(db, turn)

    def confirm_stopped(self, project: str, identity: str) -> CoordinatorTurn:
        with self.workspace.connection(write=True, project_id=project) as db:
            turn = self._get(db, project, identity)
            if turn.status != "uncertain":
                raise ApplicationError("turn_changed", "This turn no longer needs recovery.", 409)
            self.sessions.finish(db, project, identity, cleanup_confirmed=True)
            turn.status = "interrupted"
            turn.notice = "You confirmed the coordinator stopped. Nothing was replayed."
            self._save(db, turn)
            return turn

    def activity_store(self, generation_id: str) -> "CoordinatorActivityStore":
        return CoordinatorActivityStore(self, generation_id)

    @staticmethod
    def owns(
        db: sqlite3.Connection, project: str, identity: str, generation_id: str | None
    ) -> bool:
        row = db.execute(
            "SELECT status,generation_id FROM coordinator_turns WHERE project_id=? AND id=?",
            (project, identity),
        ).fetchone()
        return bool(
            row and row[0] in ("starting", "running", "stopping") and row[1] == generation_id
        )


class CoordinatorActivityStore:
    def __init__(self, store: CoordinatorStore, generation_id: str):
        self.store, self.generation_id = store, generation_id

    def write(self, project: str, identity: str, updates: list[ActivityUpdate]) -> None:
        self.store.write(project, identity, updates, generation_id=self.generation_id)
