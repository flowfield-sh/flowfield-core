"""Cheap existence checks for saved browser locations, without reading result bodies."""

import sqlite3
from urllib.parse import unquote, urlsplit

from flowfield.application import Workspace
from flowfield.errors import ApplicationError


def resource_presence(store: Workspace, workspace: str, locations: list[str]) -> dict[str, bool]:
    with store.connection() as db:
        if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace,)).fetchone():
            raise ApplicationError("not_found", "Workspace not found.", 404)
        return {location: _exists(db, workspace, location) for location in locations}


def _exists(db: sqlite3.Connection, workspace: str, location: str) -> bool:
    parts = [unquote(part) for part in urlsplit(location).path.strip("/").split("/")]
    if parts[:1] == ["workspaces"]:
        if len(parts) < 2 or parts[1] != workspace:
            return False
        parts = parts[2:]
    if len(parts) < 2:
        return True  # Collections and global settings have no resource identity.
    kind, identity = parts[:2]
    if kind == "projects":
        return identity == "new" or bool(
            db.execute(
                "SELECT 1 FROM workspace_projects WHERE workspace_id=? AND project_id=?",
                (workspace, identity),
            ).fetchone()
        )
    if kind == "tasks":
        return bool(
            db.execute(
                "SELECT 1 FROM tasks WHERE workspace_id=? AND (id=? OR key=?)",
                (workspace, identity, identity),
            ).fetchone()
        )
    if kind == "documents":
        if len(parts) == 4 and parts[2] == "revisions":
            return bool(
                db.execute(
                    "SELECT 1 FROM workspace_document_revisions "
                    "WHERE workspace_id=? AND id=? AND revision=?",
                    (workspace, identity, parts[3]),
                ).fetchone()
            )
        # Soft deletion preserves reading/restoration and exact saved history.
        return bool(
            db.execute(
                "SELECT 1 FROM workspace_documents WHERE workspace_id=? AND id=?",
                (workspace, identity),
            ).fetchone()
        )
    table = "result_versions" if kind == "results" else None
    if kind == "requests" and len(parts) == 3:
        table = {
            "question": "questions",
            "result": "result_versions",
            "attempt": "runs",
            "integration": "integrations",
            "permission": "agent_permissions",
        }.get(identity)
        identity = parts[2]
    if table:
        return bool(
            db.execute(
                f"SELECT 1 FROM {table} WHERE workspace_id=? AND id=?", (workspace, identity)
            ).fetchone()
        )
    return True
