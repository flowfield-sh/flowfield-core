"""Workspace reviewer defaults and one entity override, independent of delivery authority."""

import sqlite3
from typing import Literal

from pydantic import Field

from flowfield.agent_models import AgentChoice, EffectiveAgent
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.harness_settings import HarnessSettings
from flowfield.workspaces import Workspaces


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

    @staticmethod
    def _read(db: sqlite3.Connection, workspace: str, entity: str) -> ReviewerSettings:
        row = db.execute(
            "SELECT data FROM reviewer_settings WHERE workspace_id=? AND entity_id=?",
            (workspace, entity),
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
        entity: str = "",
    ) -> ReviewerSettingsView:
        Workspaces.read(db, workspace)
        entity = self.store._task(db, workspace, entity).id if entity else ""
        return self.resolved(db, workspace, entity)

    @classmethod
    def resolved(
        cls,
        db: sqlite3.Connection,
        workspace: str,
        entity: str = "",
    ) -> ReviewerSettingsView:
        """Resolve an already canonical scope inside an evidence transaction."""
        defaults = cls._read(db, workspace, "")
        override = cls._read(db, workspace, entity) if entity else None
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

    def get(self, workspace: str, *, entity: str = "") -> ReviewerSettingsView:
        with self.store.connection() as db:
            return self.resolve(db, workspace, entity)

    def configure(
        self,
        workspace: str,
        request: ReviewerEdit,
        *,
        entity: str = "",
    ) -> ReviewerSettingsView:
        with self.store.connection(write=True, workspace_id=workspace) as db:
            Workspaces.read(db, workspace)
            entity = self.store._task(db, workspace, entity).id if entity else ""
            current = self._read(db, workspace, entity)
            self.store._current(current.revision, request.expected_revision)
            if not entity and request.enabled is None:
                raise ApplicationError(
                    "review_default_required", "Choose whether workspace reviews are enabled."
                )
            value = ReviewerSettings(
                revision=current.revision + 1, enabled=request.enabled, selection=request.selection
            )
            db.execute(
                "INSERT INTO reviewer_settings VALUES (?,?,?) ON CONFLICT "
                "(workspace_id,entity_id) DO UPDATE SET data=excluded.data",
                (workspace, entity, value.model_dump_json()),
            )
            return self.resolve(db, workspace, entity)
