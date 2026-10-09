"""One service-host registration per concrete harness; no native credential storage."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast, get_args

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessEdit, HarnessKind, HarnessRegistration
from flowfield.storage import acquire_lock, connect, require_supported, version

KINDS = cast(tuple[HarnessKind, ...], get_args(HarnessKind))


def offline_registration(directory: Path, harness: HarnessKind) -> HarnessRegistration:
    """Offline installer/status reads never initialize or migrate the application state."""
    if harness not in KINDS:
        raise ApplicationError("unsupported_harness", "This harness is not supported.", 404)
    database = directory / "workspace.sqlite3"
    if not database.exists():
        return HarnessRegistration(harness=harness)
    with (
        acquire_lock(directory, ".initialize.lock", shared=True),
        closing(connect(database, readonly=True)) as db,
    ):
        db.execute("BEGIN")
        actual = version(db)
        require_supported(actual)
        return (
            HarnessSettings.read(db, harness)
            if actual >= 45
            else HarnessRegistration(harness=harness)
        )


class HarnessSettings:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    @staticmethod
    def read(db: sqlite3.Connection, harness: HarnessKind) -> HarnessRegistration:
        if harness not in KINDS:
            raise ApplicationError("unsupported_harness", "This harness is not supported.", 404)
        row = db.execute(
            "SELECT revision,executable,config_directory FROM harness_settings WHERE harness=?",
            (harness,),
        ).fetchone()
        if row is None:
            raise ApplicationError(
                "invalid_database", "Host harness configuration is missing.", 409
            )
        return HarnessRegistration(
            harness=harness, revision=row[0], executable=row[1], config_directory=row[2]
        )

    def get(self, harness: HarnessKind) -> HarnessRegistration:
        with self.workspace.connection() as db:
            return self.read(db, harness)

    def all(self) -> list[HarnessRegistration]:
        with self.workspace.connection() as db:
            return [self.read(db, harness) for harness in KINDS]

    def edit(self, harness: HarnessKind, request: HarnessEdit) -> HarnessRegistration:
        # Missing paths may be saved during setup; launch/readiness validates their
        # current state. Never create a directory or edit native configuration here.
        with self.workspace.connection(write=True) as db:
            current = self.read(db, harness)
            self.workspace._current(current.revision, request.expected_revision)
            db.execute(
                "UPDATE harness_settings SET revision=revision+1,executable=?,config_directory=? "
                "WHERE harness=?",
                (request.executable, request.config_directory, harness),
            )
            return self.read(db, harness)
