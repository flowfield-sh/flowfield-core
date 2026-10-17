"""Durable workspace outputs, independent of attempt filesystem lifetime."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE output_files (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), "
        "run_id TEXT NOT NULL REFERENCES runs(id), data TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX output_workspace ON output_files(workspace_id,number)")
    db.execute("CREATE INDEX output_run ON output_files(run_id,number)")
