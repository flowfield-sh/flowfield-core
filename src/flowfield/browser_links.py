"""Canonical human-facing locations, independent of HTTP API routes."""

import sqlite3
from urllib.parse import quote


def project_href(db: sqlite3.Connection, identity: str) -> str:
    owner = db.execute(
        "SELECT workspace_id FROM workspace_projects WHERE project_id=?", (identity,)
    ).fetchone()[0]
    return f"/workspaces/{quote(owner, safe='')}/projects/{quote(identity, safe='')}"
