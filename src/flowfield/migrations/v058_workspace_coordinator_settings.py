"""Move coordinator preferences to the workspace without changing active choices."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE workspace_coordinator_settings (workspace_id TEXT PRIMARY KEY "
        "REFERENCES workspaces(id), revision INTEGER NOT NULL, selection TEXT)"
    )
    # The supported upgrade creates one workspace per existing project. Never pick
    # one project's settings arbitrarily if incompatible intermediate state is supplied.
    if db.execute(
        "SELECT 1 FROM agent_settings a JOIN workspace_projects p ON p.project_id=a.project_id "
        "WHERE a.role='coordinator' AND a.scope='' GROUP BY p.workspace_id HAVING count(*)>1"
    ).fetchone():
        raise ValueError("Workspace has multiple project coordinator preferences")
    db.execute(
        "INSERT INTO workspace_coordinator_settings SELECT p.workspace_id,a.revision,a.selection "
        "FROM agent_settings a JOIN workspace_projects p ON p.project_id=a.project_id "
        "WHERE a.role='coordinator' AND a.scope=''"
    )
    db.execute(
        "DELETE FROM agent_settings WHERE role='coordinator' AND scope='' AND project_id IN "
        "(SELECT project_id FROM workspace_projects)"
    )
