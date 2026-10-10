"""Schema 52: pi harness."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    # Rebuild only the two constrained registries; preserve saved settings and
    # uncertain catalog ownership exactly. Application history is untouched.
    db.execute("ALTER TABLE harness_settings RENAME TO previous_harness_settings")
    db.execute(
        "CREATE TABLE harness_settings (harness TEXT PRIMARY KEY "
        "CHECK(harness IN ('codex','claude-code','pi')), revision INTEGER NOT NULL "
        "CHECK(revision>=1), executable TEXT, config_directory TEXT)"
    )
    db.execute("INSERT INTO harness_settings SELECT * FROM previous_harness_settings")
    db.execute("INSERT INTO harness_settings VALUES ('pi',1,NULL,NULL)")
    db.execute("DROP TABLE previous_harness_settings")
    db.execute("ALTER TABLE harness_catalogs RENAME TO previous_harness_catalogs")
    db.execute(
        "CREATE TABLE harness_catalogs (harness TEXT PRIMARY KEY "
        "CHECK(harness IN ('codex','claude-code','pi')), data TEXT NOT NULL)"
    )
    db.execute("INSERT INTO harness_catalogs SELECT * FROM previous_harness_catalogs")
    db.execute("DROP TABLE previous_harness_catalogs")
