"""Freeze the adjacent browser view as relevance, never tool authority."""

import sqlite3

from flowfield.application import Workspace
from flowfield.coordinator_models import (
    CoordinatorTaskSelection,
    CoordinatorViewContext,
    CoordinatorViewSelection,
)
from flowfield.documents import Documents
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
            and (
                (selection.milestone_id and selection.surface != "milestone")
                or selection.label_id
                or selection.without_milestone
            )
        )
        or (
            selection.surface in {"projects", "milestones", "milestone", "documents", "document"}
            and selection.project_id
        )
        or (
            selection.surface == "milestone"
            and (not selection.milestone_id or not selection.milestone_revision)
        )
        or (
            selection.surface != "milestone"
            and (selection.milestone_revision is not None or selection.verification_id is not None)
        )
        or (
            selection.surface == "document"
            and (not selection.document_id or not selection.document_revision)
        )
        or (
            selection.surface != "document"
            and (selection.document_id is not None or selection.document_revision is not None)
        )
        or (
            selection.surface == "request"
            and (not selection.request_kind or not selection.request_id)
        )
        or (selection.surface != "request" and (selection.request_kind or selection.request_id))
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
    if selection.request_kind and selection.request_id:
        from flowfield.requests import get_request

        request = get_request(
            store, workspace_id, selection.request_kind, selection.request_id, selection.project_id
        )
        value.request_title = request.title
        value.request_status = request.status
        value.request_href = request.request_href
    if selection.document_id:
        document = Documents.read(
            db, workspace_id, selection.document_id, selection.document_revision
        )
        value.document_title = document.title
    if selection.project_id:
        Workspaces.check_project(db, workspace_id, selection.project_id)
        project = store._project(db, selection.project_id)
        value.project_name, value.project_revision = project.name, project.revision
    if selection.milestone_id:
        milestone = (
            Milestones.revision(
                db, workspace_id, selection.milestone_id, selection.milestone_revision
            )
            if selection.milestone_revision
            else Milestones.read(db, workspace_id, selection.milestone_id)
        )
        value.milestone_title, value.milestone_revision = milestone.title, milestone.revision
    if selection.verification_id:
        from flowfield.milestone_verification import read as read_verification

        verification = read_verification(db, workspace_id, selection.verification_id)
        if verification.milestone_id != milestone.id:
            raise ApplicationError(
                "invalid_view_context", "Verification belongs to another milestone."
            )
        value.verification_revision = verification.revision
        value.verification_status = verification.status
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
    fields = set(CoordinatorViewSelection.model_fields)
    if requested.surface != "milestone":
        fields.discard("milestone_revision")
    return saved.model_dump(include=fields) == requested.model_dump(include=fields)
