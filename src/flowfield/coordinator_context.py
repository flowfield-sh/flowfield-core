"""Freeze the adjacent browser view as relevance, never tool authority."""

import sqlite3

from flowfield.application import Workspace
from flowfield.coordinator_models import (
    CoordinatorTaskSelection,
    CoordinatorViewContext,
    CoordinatorViewSelection,
)
from flowfield.errors import ApplicationError
from flowfield.labels import Labels
from flowfield.milestones import Milestones
from flowfield.workspaces import Workspaces


def freeze_view(
    store: Workspace,
    db: sqlite3.Connection,
    workspace_id: str,
    selection: CoordinatorViewSelection | None,
    task: CoordinatorTaskSelection | None,
) -> CoordinatorViewContext | None:
    if selection is None:
        return None  # Older saved messages and standalone sends have no browser surface.
    filtered = selection.surface in {"board", "archive"}
    if (
        (bool(task) != (selection.surface == "task"))
        or (task and task.project_id != selection.project_id)
        or (selection.milestone_id and selection.without_milestone)
        or (
            not filtered
            and (selection.milestone_id or selection.label_id or selection.without_milestone)
        )
        or (selection.surface in {"projects", "milestones"} and selection.project_id)
    ):
        raise ApplicationError(
            "invalid_view_context", "The message view does not match its context."
        )
    workspace = Workspaces.read(db, workspace_id)
    value = CoordinatorViewContext(
        **selection.model_dump(),
        workspace_name=workspace.name,
        workspace_revision=workspace.revision,
    )
    if selection.project_id:
        Workspaces.check_project(db, workspace_id, selection.project_id)
        project = store._project(db, selection.project_id)
        value.project_name, value.project_revision = project.name, project.revision
    if selection.milestone_id:
        milestone = Milestones.read(db, workspace_id, selection.milestone_id)
        value.milestone_title, value.milestone_revision = milestone.title, milestone.revision
    if selection.label_id:
        label = Labels.read(db, workspace_id, selection.label_id)
        if label.deleted_at:
            raise ApplicationError(
                "view_context_changed",
                "The selected label was deleted. Refresh the view and send again.",
                409,
            )
        value.label_name, value.label_revision = label.name, label.revision
    return value


def same_view(
    saved: CoordinatorViewContext | None, requested: CoordinatorViewSelection | None
) -> bool:
    if saved is None or requested is None:
        return saved is None and requested is None
    return (
        saved.model_dump(include=set(CoordinatorViewSelection.model_fields))
        == requested.model_dump()
    )
