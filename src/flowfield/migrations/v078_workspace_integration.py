"""Workspace delivery defaults; retain every explicit milestone choice."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE workspace_integration_policies (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        revision INTEGER NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY(workspace_id, revision)
    )""")
    # Revision one is the untouched, default-off policy, not a human override.
    db.execute("""UPDATE milestone_deliveries SET data=json_set(
        data,'$.integration_policy.enabled',NULL)
        WHERE COALESCE(json_extract(data,'$.integration_policy.revision'),1)=1
        AND COALESCE(json_extract(data,'$.integration_policy.enabled'),0)=0""")
