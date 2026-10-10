"""Schema 46: harness catalogs."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE harness_catalogs (harness TEXT PRIMARY KEY "
        "CHECK(harness IN ('codex','claude-code')), data TEXT NOT NULL)"
    )
