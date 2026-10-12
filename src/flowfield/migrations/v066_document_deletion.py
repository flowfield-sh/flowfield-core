"""Retain document deletion attribution without removing immutable revisions."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE workspace_documents ADD COLUMN deleted_at TEXT")
    db.execute("ALTER TABLE workspace_documents ADD COLUMN deleted_by TEXT")
