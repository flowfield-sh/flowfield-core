"""One resolution path; existing worker defaults and queue revisions retain their owner."""

import sqlite3

from flowfield.agent_models import (
    AgentChoice,
    AgentRole,
    AgentSettingsEdit,
    AgentSettingsView,
    EffectiveAgent,
)
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerSettings
from flowfield.harness_settings import HarnessSettings


class AgentSettings:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _read(
        self, db: sqlite3.Connection, project: str, role: AgentRole, scope: str = ""
    ) -> AgentSettingsView:
        self.workspace._project(db, project)
        if scope and role == "worker":
            scope = self.workspace._task(db, project, scope).id
        if role == "worker" and not scope:
            row = db.execute(
                "SELECT data FROM worker_settings WHERE project_id=?", (project,)
            ).fetchone()
            current = (
                WorkerSettings.model_validate_json(row[0])
                if row
                else WorkerSettings(project_id=project)
            )
            return AgentSettingsView(
                revision=current.revision,
                selection=current.selection,
            )
        if self.workspace.schema_version < 32:
            return AgentSettingsView()  # Baseline construction for migration verification.
        row = db.execute(
            "SELECT revision,selection FROM agent_settings WHERE project_id=? AND role=? "
            "AND scope=?",
            (project, role, scope),
        ).fetchone()
        return (
            AgentSettingsView(
                revision=row[0],
                selection=AgentChoice.model_validate_json(row[1]) if row[1] else None,
            )
            if row
            else AgentSettingsView()
        )

    def resolve(
        self, db: sqlite3.Connection, project: str, role: AgentRole, scope: str = ""
    ) -> AgentSettingsView:
        if role == "coordinator":
            scope = ""
        defaults = self._read(db, project, role)
        override = self._read(db, project, role, scope) if scope else None
        selected = override.selection if override and override.selection else defaults.selection
        result = (override or defaults).model_copy()
        if selected:
            result.effective = EffectiveAgent(
                choice=selected,
                source="override" if override and override.selection else "project",
                default_revision=defaults.revision,
                override_revision=override.revision if override else None,
            )
        return result

    def get(self, project: str, role: AgentRole, scope: str = "") -> AgentSettingsView:
        with self.workspace.connection() as db:
            return self.resolve(db, project, role, scope)

    def freeze(self, db: sqlite3.Connection, effective: EffectiveAgent) -> EffectiveAgent:
        if self.workspace.schema_version < 45:
            return effective  # Frozen schema-44 fixture construction for migration evidence.
        return effective.model_copy(
            update={"registration": HarnessSettings.read(db, effective.choice.harness)}
        )

    def edit(
        self, project: str, role: AgentRole, request: AgentSettingsEdit, scope: str = ""
    ) -> AgentSettingsView:
        if request.selection and request.selection.fast is None:
            request = request.model_copy(
                update={"selection": request.selection.model_copy(update={"fast": False})}
            )
        if (
            role == "coordinator"
            and request.selection
            and request.selection.harness == "codex"
            and request.selection.mode is None
        ):
            # Old clients selected only model/effort. Preserve their existing access.
            request = request.model_copy(
                update={"selection": request.selection.model_copy(update={"mode": "read-only"})}
            )
        if role == "worker" and not scope:
            raise ApplicationError(
                "worker_defaults", "Use worker settings to edit project defaults."
            )
        if role == "coordinator":
            scope = ""
        with self.workspace.connection(write=True, project_id=project) as db:
            if role == "worker" and scope:
                scope = self.workspace._task(db, project, scope).id
            current = self._read(db, project, role, scope)
            self.workspace._current(current.revision, request.expected_revision)
            db.execute(
                "INSERT INTO agent_settings VALUES (?,?,?,?,?) "
                "ON CONFLICT(project_id,role,scope) DO UPDATE SET "
                "revision=excluded.revision,selection=excluded.selection",
                (
                    project,
                    role,
                    scope,
                    current.revision + 1,
                    request.selection.model_dump_json() if request.selection else None,
                ),
            )
            return self.resolve(db, project, role, scope)
