"""Ordered database-only upgrades from the Flowfield schema-44 baseline."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

BASELINE_VERSION = 44


@dataclass(frozen=True)
class Migration:
    version: int
    apply: Callable[[sqlite3.Connection], None]


def host_harnesses(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE harness_settings (harness TEXT PRIMARY KEY "
        "CHECK(harness IN ('codex','claude-code')), revision INTEGER NOT NULL "
        "CHECK(revision>=1), executable TEXT, config_directory TEXT)"
    )
    db.executemany(
        "INSERT INTO harness_settings VALUES (?,1,NULL,NULL)", [("codex",), ("claude-code",)]
    )
    db.execute("ALTER TABLE coordinator_sessions ADD COLUMN launch TEXT")


def harness_catalogs(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE harness_catalogs (harness TEXT PRIMARY KEY "
        "CHECK(harness IN ('codex','claude-code')), data TEXT NOT NULL)"
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(45, host_harnesses),
    Migration(46, harness_catalogs),
)


def current_version() -> int:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions))):
        raise RuntimeError("Database migrations must be contiguous and ordered.")
    return versions[-1] if versions else BASELINE_VERSION
