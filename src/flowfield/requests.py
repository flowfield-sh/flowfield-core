"""Stable human request links over canonical records, without another response lifecycle."""

import sqlite3
from typing import cast
from urllib.parse import quote

from flowfield.application import Workspace
from flowfield.attention import AttentionItem, RequestKind, attention_page
from flowfield.browser_links import request_href, workspace_href
from flowfield.errors import ApplicationError


def bind_links(db: sqlite3.Connection, item: AttentionItem, workspace_id: str | None) -> None:
    workspace_id = workspace_id or item.workspace_id
    if not workspace_id:
        return
    base = f"/workspaces/{quote(workspace_id, safe='')}"
    item.source_key = item.task_key
    if item.task_key and item.workspace_id:
        item.source_href = f"{workspace_href(item.workspace_id)}/tasks/{item.task_key}"
    else:
        item.source_href = (
            workspace_href(item.workspace_id) if item.workspace_id else base + "/inbox"
        )
    if item.kind != "document":
        item.request_kind = cast(
            RequestKind, "attempt" if item.kind in {"review", "intervention"} else item.kind
        )
        item.request_href = request_href(workspace_id, item.kind, item.id)


def get_request(
    workspace: Workspace,
    workspace_id: str,
    kind: RequestKind,
    identity: str,
) -> AttentionItem:
    page = attention_page(
        workspace,
        workspace_id,
        "history",
        request_kind=kind,
        request_id=identity,
        limit=1,
    )
    if not page.items:
        raise ApplicationError("request_not_found", "Request not found in this workspace.", 404)
    return page.items[0]
