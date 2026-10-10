"""Schema 53: coordinator welcome."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE coordinator_conversations ADD COLUMN welcome TEXT NOT NULL DEFAULT ''")
