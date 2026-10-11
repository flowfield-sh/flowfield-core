"""Retire disposable notification history; keep update preferences and workspace data."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE update_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
    db.execute(
        "INSERT INTO update_state SELECT 1,json_remove(data,'$.announced_version') "
        "FROM notification_state WHERE name='updates'"
    )
    db.execute("DROP TABLE notification_deliveries")
    db.execute("DROP TABLE notifications")
    db.execute("DROP TABLE notification_state")
    db.execute(
        "CREATE TABLE announcement_preferences (id INTEGER PRIMARY KEY CHECK(id=1), "
        "data TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE attention_sources (key TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "signature TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX attention_sources_project ON attention_sources(project_id)")
    db.execute(
        "CREATE TABLE announcement_events (id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT "
        "NOT NULL, project_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL, "
        "created_at REAL NOT NULL, "
        "state TEXT NOT NULL DEFAULT 'pending', client_id TEXT, channel TEXT)"
    )
    db.execute("CREATE INDEX announcement_pending ON announcement_events(state,created_at)")
    db.execute(
        "CREATE TABLE announcement_clients (id TEXT PRIMARY KEY, seen_at REAL NOT NULL, "
        "joined_at REAL NOT NULL, active_since REAL NOT NULL, data TEXT NOT NULL)"
    )
