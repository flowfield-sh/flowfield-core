"""Schema 45: host harnesses."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE harness_settings (harness TEXT PRIMARY KEY "
        "CHECK(harness IN ('codex','claude-code')), revision INTEGER NOT NULL "
        "CHECK(revision>=1), executable TEXT, config_directory TEXT)"
    )
    db.executemany(
        "INSERT INTO harness_settings VALUES (?,1,NULL,NULL)", [("codex",), ("claude-code",)]
    )
    db.execute("ALTER TABLE coordinator_sessions ADD COLUMN launch TEXT")
