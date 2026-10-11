"""Versioned workspace Markdown; no repository guidance is discovered or imported."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE workspace_documents (workspace_id TEXT NOT NULL REFERENCES workspaces(id), "
        "id TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(workspace_id,id))"
    )
    db.execute(
        "CREATE TABLE workspace_document_revisions (workspace_id TEXT NOT NULL, "
        "id TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, "
        "created_at TEXT NOT NULL, author TEXT NOT NULL, restored_from INTEGER, "
        "PRIMARY KEY(workspace_id,id,revision), FOREIGN KEY(workspace_id,id) "
        "REFERENCES workspace_documents(workspace_id,id))"
    )
    db.execute("INSERT INTO workspace_documents SELECT id,'guidance',1 FROM workspaces")
    db.execute(
        "INSERT INTO workspace_document_revisions SELECT id,'guidance',1,'Workspace guidance',"
        "'',strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),'migration',NULL FROM workspaces"
    )
