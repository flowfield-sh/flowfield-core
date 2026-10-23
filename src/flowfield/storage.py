"""Exclusive fresh-state initialization and current-schema validation."""

import fcntl
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

from flowfield.errors import ApplicationError
from flowfield.schema import SCHEMA_VERSION

DATABASE = "workspace.sqlite3"


def acquire_lock(directory: Path, name: str, *, shared: bool = False) -> BinaryIO:
    lock = (directory / name).open("ab")
    try:
        fcntl.flock(lock, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except BaseException as error:
        lock.close()
        if isinstance(error, BlockingIOError):
            raise ApplicationError(
                "storage_busy",
                "Another Flowfield service or storage operation owns this "
                "workspace. Stop the service and retry.",
                409,
            ) from error
        raise
    return lock


def connect(database: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if database.is_symlink():
        raise ApplicationError("invalid_database", "Database must not be a symlink.")
    db = sqlite3.connect(
        database.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
        uri=True,
        timeout=5,
        isolation_level=None,
    )
    db.execute("PRAGMA foreign_keys = ON")
    return db


def version(db: sqlite3.Connection) -> int:
    return int(db.execute("PRAGMA user_version").fetchone()[0])


def require_supported(value: int) -> None:
    if value != SCHEMA_VERSION:
        raise ApplicationError(
            "unsupported_schema",
            f"Unsupported database version {value}; this build requires {SCHEMA_VERSION}. "
            "Preserve this data directory and use a Flowfield version compatible with its schema.",
            409,
        )


def require_current(db: sqlite3.Connection, expected: int) -> None:
    actual = version(db)
    if actual != expected:
        raise ApplicationError(
            "schema_changed",
            f"Database schema is {actual}; this workspace opened schema {expected}. "
            "Stop the service and use a Flowfield version compatible with this saved store.",
            409,
        )


def integrity(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise ApplicationError("invalid_database", "Database integrity check failed.", 409)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ApplicationError("invalid_database", "Database contains broken references.", 409)


def identity(db: sqlite3.Connection) -> tuple[str, int]:
    row = db.execute("SELECT workspace_id, revision FROM storage_metadata WHERE id=1").fetchone()
    if row is None:
        raise ApplicationError("invalid_database", "Database storage identity is missing.", 409)
    return str(row[0]), int(row[1])


def initialize(directory: Path, schema: str) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = directory / DATABASE
    try:
        owner = acquire_lock(directory, ".execution.lock")
    except ApplicationError:
        # Cooperating readers may open current state while the service owns execution.
        if not database.exists():
            raise
        with (
            acquire_lock(directory, ".initialize.lock", shared=True),
            closing(connect(database, readonly=True)) as db,
        ):
            require_supported(version(db))
            identity(db)
        return
    with owner, acquire_lock(directory, ".initialize.lock"):
        if database.is_symlink():
            raise ApplicationError("invalid_database", "Workspace database must not be a symlink.")
        if not database.exists():
            database.touch(mode=0o600)
        with closing(connect(database)) as db:
            current = version(db)
            if current:
                require_supported(current)
                identity(db)
                return
            if db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                raise ApplicationError(
                    "unsupported_schema",
                    "Unversioned nonempty database. Preserve this data directory. "
                    "Use a Flowfield version compatible with its schema to inspect it.",
                    409,
                )
            try:
                db.executescript("BEGIN IMMEDIATE;" + schema)
                require_supported(version(db))
                integrity(db)
                identity(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise


def status(directory: Path) -> dict[str, Any]:
    actual = None
    if (directory / DATABASE).exists():
        with (
            acquire_lock(directory, ".initialize.lock", shared=True),
            closing(connect(directory / DATABASE, readonly=True)) as db,
        ):
            actual = version(db)
    return {
        "database": str(directory / DATABASE),
        "schema_version": actual,
        "supported_version": SCHEMA_VERSION,
    }
