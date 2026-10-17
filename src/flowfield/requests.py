"""Stable human request links over canonical records, without another response lifecycle."""

import sqlite3
from typing import cast
from urllib.parse import quote, urlencode

from flowfield.application import Workspace
from flowfield.attention import AttentionItem, RequestKind, attention_page
from flowfield.browser_links import project_href
from flowfield.errors import ApplicationError


def request_href(workspace_id: str, kind: str, identity: str, project_id: str | None) -> str:
    family = "attempt" if kind in {"review", "intervention"} else kind
    path = (
        f"/workspaces/{quote(workspace_id, safe='')}/requests/{family}/{quote(identity, safe='')}"
    )
    return path + ("?" + urlencode({"project_id": project_id}) if project_id else "")


def bind_links(db: sqlite3.Connection, item: AttentionItem, workspace_id: str | None) -> None:
    if workspace_id is None and item.project_id:
        row = db.execute(
            "SELECT workspace_id FROM workspace_scopes WHERE project_id=?", (item.project_id,)
        ).fetchone()
        workspace_id = row[0] if row else None
    if not workspace_id:
        return
    base = f"/workspaces/{quote(workspace_id, safe='')}"
    item.source_key = item.task_key
    if item.kind == "milestone":
        item.source_href = f"{base}/milestones/{item.task_key}?tab=overview"
    elif item.task_key and item.project_id:
        item.source_href = f"{project_href(item.project_id)}/tasks/{item.task_key}"
    else:
        item.source_href = project_href(item.project_id) if item.project_id else base + "/inbox"
    if item.kind != "document":
        item.request_kind = cast(
            RequestKind, "attempt" if item.kind in {"review", "intervention"} else item.kind
        )
        item.request_href = request_href(workspace_id, item.kind, item.id, item.project_id)


def get_request(
    workspace: Workspace,
    workspace_id: str,
    kind: RequestKind,
    identity: str,
    project_id: str | None = None,
) -> AttentionItem:
    page = attention_page(
        workspace,
        project_id,
        "history",
        workspace_id=workspace_id,
        request_kind=kind,
        request_id=identity,
        limit=1,
    )
    if not page.items:
        raise ApplicationError("request_not_found", "Request not found in this workspace.", 404)
    return page.items[0]
