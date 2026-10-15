"""Durable task allowance and explicit human check-execution grants."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE execution_policies (workspace_id TEXT PRIMARY KEY REFERENCES "
        "workspaces(id), data TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE task_execution_limits (project_id TEXT NOT NULL, task_id TEXT "
        "NOT NULL, blocked TEXT, data TEXT NOT NULL, PRIMARY KEY(project_id,task_id), "
        "FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id))"
    )
    db.execute(
        "CREATE TABLE worker_check_grants (project_id TEXT PRIMARY KEY REFERENCES "
        "projects(id), data TEXT NOT NULL)"
    )
