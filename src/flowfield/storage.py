"""Exclusive initialization, verified upgrades and current-schema validation."""

import fcntl
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from flowfield.errors import ApplicationError
from flowfield.schema import SCHEMA_VERSION, TASK_DELETIONS, TASK_REVIEWS

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


def upgrade(directory: Path, db: sqlite3.Connection, current: int) -> None:
    """Schema 86 shipped in 0.6/0.7; this migration changes only the database."""
    if current not in (86, 87, 88):
        require_supported(current)
        return
    integrity(db)
    identity(db)
    backups = directory / "backups"
    if backups.is_symlink():
        raise ApplicationError("invalid_backup", "Recovery directory must not be a symlink.")
    backups.mkdir(mode=0o700, exist_ok=True)
    backup = backups / f"schema-{current}-{uuid4().hex}.sqlite3"
    backup.touch(mode=0o600, exist_ok=False)
    with closing(sqlite3.connect(backup)) as saved:
        db.backup(saved)
        integrity(saved)
        if version(saved) != current or identity(saved) != identity(db):
            raise ApplicationError("backup_failed", "Database recovery copy could not be verified.")
    with backup.open("rb") as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(backups, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("BEGIN IMMEDIATE")
        if current == 86:
            db.execute(TASK_DELETIONS)
        if current < 88:
            sequences = db.execute("SELECT name,seq FROM sqlite_sequence").fetchall()
            db.execute(
                TASK_REVIEWS.replace(
                    "CREATE TABLE task_reviews", "CREATE TABLE updated_task_reviews"
                )
            )
            db.execute("INSERT INTO updated_task_reviews SELECT * FROM task_reviews")
            db.execute("DROP TABLE task_reviews")
            db.execute("ALTER TABLE updated_task_reviews RENAME TO task_reviews")
            db.execute(
                "CREATE INDEX task_reviews_task ON task_reviews(workspace_id, task_id, number)"
            )
            db.execute("DELETE FROM sqlite_sequence")
            db.executemany("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", sequences)
        # New results may bind a reconciled task revision. Keep every old row unchanged,
        # but prevent an older service from ignoring that agreement during delivery.
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        integrity(db)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA foreign_keys=ON")


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
                upgrade(directory, db, current)
                require_supported(version(db))
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
