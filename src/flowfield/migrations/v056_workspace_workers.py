"""Schema 56: workspace worker defaults/queue, repository-local scheduling problems."""

import json
import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE workspace_worker_settings (workspace_id TEXT PRIMARY KEY "
        "REFERENCES workspaces(id), data TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE project_worker_problems (project_id TEXT PRIMARY KEY "
        "REFERENCES projects(id), problem TEXT NOT NULL)"
    )
    for project, workspace, data in db.execute(
        "SELECT m.project_id,m.workspace_id,s.data FROM workspace_projects m "
        "JOIN worker_settings s ON s.project_id=m.project_id"
    ).fetchall():
        values = json.loads(data)
        values.pop("project_id")
        problem = values.pop("problem", None)
        values["workspace_id"] = workspace
        db.execute(
            "INSERT INTO workspace_worker_settings VALUES (?,?)",
            (workspace, json.dumps(values)),
        )
        if problem:
            db.execute("INSERT INTO project_worker_problems VALUES (?,?)", (project, problem))
        db.execute("DELETE FROM worker_settings WHERE project_id=?", (project,))
