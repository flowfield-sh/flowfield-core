"""Review configuration and durable candidate-bound assessments; no automatic scheduling."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE reviewer_settings (workspace_id TEXT NOT NULL REFERENCES workspaces(id), "
        "purpose TEXT NOT NULL CHECK(purpose IN ('task','milestone')), project_id TEXT NOT NULL, "
        "entity_id TEXT NOT NULL, data TEXT NOT NULL, "
        "PRIMARY KEY(workspace_id,purpose,project_id,entity_id))"
    )
    db.execute(
        "CREATE TABLE task_reviews (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, "
        "project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL, "
        "result_id TEXT NOT NULL REFERENCES result_versions(id), binding TEXT NOT NULL, "
        "status TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(result_id,binding), "
        "FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id))"
    )
    db.execute("CREATE INDEX task_reviews_task ON task_reviews(project_id,task_id,number)")
