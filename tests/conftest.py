"""Ordinary tests never contact the package registry implicitly."""

from contextlib import contextmanager

import pytest


@pytest.fixture(autouse=True)
def no_automatic_registry_requests(monkeypatch):
    monkeypatch.setenv("FLOWFIELD_UPDATE_CHECKS", "0")


@pytest.fixture(autouse=True)
def old_writer_scope_fixture(monkeypatch):
    """Migration fixtures reconstruct old writers with current task helpers.

    The old schema has only project scopes. Supply their read alias locally, without
    changing persisted schemas, migration fingerprints or production compatibility.
    """
    from flowfield.application import Workspace

    original = Workspace.connection

    @contextmanager
    def connection(self, **kwargs):
        with original(self, **kwargs) as db:
            if (
                self.schema_version < 80
                and db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='workspace_projects'"
                ).fetchone()
            ):
                db.execute(
                    "CREATE TEMP VIEW IF NOT EXISTS workspace_scopes AS "
                    "SELECT project_id,workspace_id FROM workspace_projects"
                )
            yield db

    monkeypatch.setattr(Workspace, "connection", connection)
