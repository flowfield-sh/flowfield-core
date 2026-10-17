"""Mutable library organization is separate from immutable submitted outputs."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE output_locations (file_id TEXT PRIMARY KEY REFERENCES output_files(id), "
        "path TEXT NOT NULL, revision INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE output_folders (workspace_id TEXT NOT NULL REFERENCES workspaces(id), "
        "path TEXT NOT NULL, PRIMARY KEY(workspace_id,path))"
    )
