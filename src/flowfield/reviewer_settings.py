"""Workspace reviewer defaults and one entity override, independent of delivery authority."""

import sqlite3
from typing import Literal

from pydantic import Field

from flowfield.agent_models import AgentChoice, EffectiveAgent
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.harness_settings import HarnessSettings
from flowfield.milestones import Milestones
from flowfield.worker_settings import workspace_id
from flowfield.workspaces import Workspaces

ReviewPurpose = Literal["task", "milestone"]


class ReviewerSettings(Record):
    revision: int = 1
    enabled: bool | None = None
    selection: AgentChoice | None = None


class ReviewerEdit(Record):
    expected_revision: int = Field(ge=1)
    enabled: bool | None = None
    selection: AgentChoice | None = None


class EffectiveReviewer(Record):
    enabled: bool
    enabled_source: Literal["workspace", "override"]
    default_revision: int
    override_revision: int | None = None
    agent: EffectiveAgent | None = None


class ReviewerSettingsView(ReviewerSettings):
    effective: EffectiveReviewer


class Reviewers:
    def __init__(self, store: Workspace):
        self.store = store

    def _scope(
        self,
        db: sqlite3.Connection,
        workspace: str,
        purpose: ReviewPurpose,
        project: str = "",
        entity: str = "",
    ) -> tuple[str, str]:
        Workspaces.read(db, workspace)
        if not entity:
            if project:
                raise ApplicationError("review_scope", "A task override requires a task.")
            return "", ""
        if purpose == "task":
            if workspace_id(db, project) != workspace:
                raise ApplicationError(
                    "review_scope", "Task project is outside this workspace.", 404
                )
            return project, self.store._task(db, project, entity).id
        if project:
            raise ApplicationError("review_scope", "Milestone reviewers have workspace scope.")
        return "", Milestones.read(db, workspace, entity).id

    @staticmethod
    def _read(
        db: sqlite3.Connection, workspace: str, purpose: ReviewPurpose, project: str, entity: str
    ) -> ReviewerSettings:
        row = db.execute(
            "SELECT data FROM reviewer_settings WHERE workspace_id=? AND purpose=? "
            "AND project_id=? AND entity_id=?",
            (workspace, purpose, project, entity),
        ).fetchone()
        return (
            ReviewerSettings.model_validate_json(row[0])
            if row
            else ReviewerSettings(enabled=None if entity else True)
        )

    def resolve(
        self,
        db: sqlite3.Connection,
        workspace: str,
        purpose: ReviewPurpose,
        project: str = "",
        entity: str = "",
    ) -> ReviewerSettingsView:
        project, entity = self._scope(db, workspace, purpose, project, entity)
        return self.resolved(db, workspace, purpose, project, entity)

    @classmethod
    def resolved(
        cls,
        db: sqlite3.Connection,
        workspace: str,
        purpose: ReviewPurpose,
        project: str = "",
        entity: str = "",
    ) -> ReviewerSettingsView:
        """Resolve an already canonical scope inside an evidence transaction."""
        defaults = cls._read(db, workspace, purpose, "", "")
        override = cls._read(db, workspace, purpose, project, entity) if entity else None
        current = override or defaults
        enabled = (
            override.enabled if override and override.enabled is not None else defaults.enabled
        )
        selection = override.selection if override and override.selection else defaults.selection
        effective = EffectiveReviewer(
            enabled=bool(enabled),
            enabled_source="override" if override and override.enabled is not None else "workspace",
            default_revision=defaults.revision,
            override_revision=override.revision if override else None,
            agent=EffectiveAgent(
                choice=selection,
                source="override" if override and override.selection else "workspace",
                default_revision=defaults.revision,
                override_revision=override.revision if override else None,
                registration=HarnessSettings.read(db, selection.harness),
            )
            if selection
            else None,
        )
        return ReviewerSettingsView(**current.model_dump(), effective=effective)

    def get(
        self, workspace: str, purpose: ReviewPurpose, *, project: str = "", entity: str = ""
    ) -> ReviewerSettingsView:
        with self.store.connection() as db:
            return self.resolve(db, workspace, purpose, project, entity)

    def configure(
        self,
        workspace: str,
        purpose: ReviewPurpose,
        request: ReviewerEdit,
        *,
        project: str = "",
        entity: str = "",
    ) -> ReviewerSettingsView:
        with self.store.connection(write=True, project_id=project or None) as db:
            project, entity = self._scope(db, workspace, purpose, project, entity)
            current = self._read(db, workspace, purpose, project, entity)
            self.store._current(current.revision, request.expected_revision)
            if not entity and request.enabled is None:
                raise ApplicationError(
                    "review_default_required", "Choose whether workspace reviews are enabled."
                )
            value = ReviewerSettings(
                revision=current.revision + 1, enabled=request.enabled, selection=request.selection
            )
            db.execute(
                "INSERT INTO reviewer_settings VALUES (?,?,?,?,?) ON CONFLICT "
                "(workspace_id,purpose,project_id,entity_id) DO UPDATE SET data=excluded.data",
                (workspace, purpose, project, entity, value.model_dump_json()),
            )
            return self.resolve(db, workspace, purpose, project, entity)
