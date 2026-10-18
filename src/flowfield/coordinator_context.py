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
from flowfield.workspaces import Workspaces


def freeze_view(
    store: Workspace,
    db: sqlite3.Connection,
    workspace_id: str,
    selection: CoordinatorViewSelection | None,
    task: CoordinatorTaskSelection | None,
) -> CoordinatorViewContext | None:
    if selection is None:
        return None  # Standalone sends have no adjacent browser surface.
    filtered = selection.surface in {"board", "archive"}
    if (
        (bool(task) != (selection.surface == "task"))
        or (selection.surface == "project" and not selection.project_id)
        or (
            task
            and selection.project_id is not None
            and store._task(db, workspace_id, task.task_id).project_id != selection.project_id
        )
        or (not filtered and selection.label_id)
        or (
            selection.surface in {"projects", "documents", "document", "files"}
            and selection.project_id
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

        request = get_request(store, workspace_id, selection.request_kind, selection.request_id)
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
    return saved.model_dump(include=fields) == requested.model_dump(include=fields)
