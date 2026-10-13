"""Retain verification snapshots and review receipts without inventing acceptance."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE milestone_verifications (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL, milestone_id TEXT NOT NULL, "
        "status TEXT NOT NULL, data TEXT NOT NULL, "
        "FOREIGN KEY(workspace_id,milestone_id) REFERENCES milestones(workspace_id,id))"
    )
    db.execute(
        "CREATE INDEX milestone_verification_history ON milestone_verifications "
        "(workspace_id,milestone_id,number DESC)"
    )
    db.execute("CREATE INDEX milestone_verification_active ON milestone_verifications(status)")
