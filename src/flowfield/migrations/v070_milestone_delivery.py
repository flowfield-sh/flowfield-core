"""Existing milestones remain groupings until delivery is explicitly configured."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE milestone_deliveries (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
        "milestone_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id), "
        "data TEXT NOT NULL, UNIQUE(workspace_id,milestone_id), "
        "FOREIGN KEY(workspace_id,milestone_id) REFERENCES milestones(workspace_id,id))"
    )
