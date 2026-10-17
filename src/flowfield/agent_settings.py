"""Resolve coordinator choices and workspace worker defaults with per-task overrides."""

import sqlite3

from flowfield import worker_settings
from flowfield.agent_models import (
    AgentChoice,
    AgentRole,
    AgentSettingsEdit,
    AgentSettingsView,
    EffectiveAgent,
)
from flowfield.application import Workspace
from flowfield.coordinator_settings import CoordinatorSettings
from flowfield.errors import ApplicationError
from flowfield.harness_settings import HarnessSettings


class AgentSettings:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _read(
        self, db: sqlite3.Connection, project: str, role: AgentRole, scope: str = ""
    ) -> AgentSettingsView:
        self.workspace._work_scope(db, project)
        if role == "coordinator":
            owner = worker_settings.workspace_id(db, project)
            if owner:
                return CoordinatorSettings.read(db, owner)
        if scope and role == "worker":
            scope = self.workspace._task(db, project, scope).id
        if role == "worker" and not scope:
            current = worker_settings.read_project(db, project)
            return AgentSettingsView(
                revision=current.revision,
                selection=current.selection,
            )

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
                source=(
                    "override"
                    if override and override.selection
                    else "workspace"
                    if (worker_settings.workspace_id(db, project))
                    else "project"
                ),
                default_revision=defaults.revision,
                override_revision=override.revision if override else None,
            )
        return result

    def get(self, project: str, role: AgentRole, scope: str = "") -> AgentSettingsView:
        with self.workspace.connection() as db:
            return self.resolve(db, project, role, scope)

    def freeze(self, db: sqlite3.Connection, effective: EffectiveAgent) -> EffectiveAgent:

        return effective.model_copy(
            update={"registration": HarnessSettings.read(db, effective.choice.harness)}
        )

    def edit(
        self, project: str, role: AgentRole, request: AgentSettingsEdit, scope: str = ""
    ) -> AgentSettingsView:
        with self.workspace.connection(
            write=True, project_id=project if role == "worker" else None
        ) as db:
            return self._edit(db, project, role, request, scope)

    def _edit(
        self,
        db: sqlite3.Connection,
        project: str,
        role: AgentRole,
        request: AgentSettingsEdit,
        scope: str = "",
    ) -> AgentSettingsView:
        request = request.model_copy(update={"selection": self.normalize(request.selection, role)})
        if role == "coordinator":
            owner = worker_settings.workspace_id(db, project)
            if owner:
                return CoordinatorSettings(self.workspace).write(db, owner, request)
            raise ApplicationError(
                "workspace_required", "Add this project to an explicit workspace first.", 409
            )
        if role == "worker" and not scope:
            raise ApplicationError("worker_defaults", "Use worker settings to edit defaults.")
        if scope:
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

    @staticmethod
    def normalize(choice: AgentChoice | None, role: AgentRole) -> AgentChoice | None:
        if choice is None:
            return None
        choice = choice.model_copy(update={"fast": bool(choice.fast)})
        if role == "coordinator" and choice.harness == "codex" and choice.mode is None:
            # Old clients selected only model/effort. Preserve their existing access.
            choice.mode = "read-only"
        return choice
