"""Explicit project document defaults; existing assignments retain their context."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE projects ADD COLUMN worker_documents TEXT NOT NULL DEFAULT '[]'")
