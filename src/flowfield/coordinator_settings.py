"""One coordinator preference per workspace, independent of repository adoption."""

import sqlite3

from flowfield.adapters.coordinator_sessions import CoordinatorSessions
from flowfield.adapters.session_continuity import retains_session
from flowfield.agent_models import AgentChoice, AgentSettingsEdit, AgentSettingsView, EffectiveAgent
from flowfield.application import Workspace
from flowfield.errors import ApplicationError


class CoordinatorSettings:
    def __init__(self, store: Workspace):
        self.store = store

    @staticmethod
    def read(db: sqlite3.Connection, identity: str) -> AgentSettingsView:
        if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (identity,)).fetchone():
            raise ApplicationError("not_found", "Workspace not found.", 404)
        row = db.execute(
            "SELECT revision,selection FROM workspace_coordinator_settings WHERE workspace_id=?",
            (identity,),
        ).fetchone()
        result = AgentSettingsView(
            revision=row[0] if row else 1,
            selection=AgentChoice.model_validate_json(row[1]) if row and row[1] else None,
        )
        if result.selection:
            result.effective = EffectiveAgent(
                choice=result.selection, source="workspace", default_revision=result.revision
            )
        return result

    def get(self, identity: str) -> AgentSettingsView:
        with self.store.connection() as db:
            return self.read(db, identity)

    def edit(self, identity: str, request: AgentSettingsEdit) -> AgentSettingsView:
        with self.store.connection(write=True) as db:
            return self.write(db, identity, request)

    def write(
        self, db: sqlite3.Connection, identity: str, request: AgentSettingsEdit
    ) -> AgentSettingsView:
        from flowfield.agent_settings import AgentSettings

        current = self.read(db, identity)
        self.store._current(current.revision, request.expected_revision)
        choice = AgentSettings.normalize(request.selection, "coordinator")
        if current.selection != choice and not retains_session(current.selection, choice):
            # Until conversation ownership moves, every member's native source must
            # be preserved and invalidated together; an uncertain sibling still owns it.
            if db.execute(
                "SELECT 1 FROM coordinator_turns t JOIN workspace_projects p "
                "ON p.project_id=t.project_id WHERE p.workspace_id=? "
                "AND t.status IN ('starting','running','stopping','uncertain') LIMIT 1",
                (identity,),
            ).fetchone():
                raise ApplicationError(
                    "coordinator_busy",
                    "Stop the coordinator and confirm cleanup before "
                    "changing harness or settings that need a fresh session.",
                    409,
                )
            for row in db.execute(
                "SELECT project_id FROM workspace_projects WHERE workspace_id=?", (identity,)
            ).fetchall():
                CoordinatorSessions.fresh(db, row[0])
        db.execute(
            "INSERT INTO workspace_coordinator_settings VALUES (?,?,?) "
            "ON CONFLICT(workspace_id) DO UPDATE SET revision=excluded.revision,"
            "selection=excluded.selection",
            (identity, current.revision + 1, choice.model_dump_json() if choice else None),
        )
        return self.read(db, identity)
