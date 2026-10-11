"""Schema 55: explicit workspaces and preserved project membership."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "description TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE workspace_projects (project_id TEXT PRIMARY KEY REFERENCES projects(id), "
        "workspace_id TEXT NOT NULL REFERENCES workspaces(id))"
    )
    db.execute("CREATE INDEX workspace_members ON workspace_projects(workspace_id, project_id)")
    # IDs live in different namespaces. Reusing the project ID gives this one-to-one
    # upgrade a deterministic mapping without rewriting any historical work identity.
    db.execute(
        "INSERT INTO workspaces SELECT id, name || ' Workspace', '', 1, "
        "created_at, updated_at, updated_by FROM projects"
    )
    db.execute("INSERT INTO workspace_projects SELECT id, id FROM projects")
