"""Exact promotion authority is independent from task approvals."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE milestone_promotions (id TEXT PRIMARY KEY, "
        "project_id TEXT NOT NULL REFERENCES projects(id), "
        "verification_id TEXT NOT NULL UNIQUE REFERENCES milestone_verifications(id), "
        "status TEXT NOT NULL, data TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX milestone_promotion_work ON milestone_promotions(project_id,status)")
    db.execute(
        "ALTER TABLE inspections ADD COLUMN promotion_id TEXT REFERENCES milestone_promotions(id)"
    )
    db.execute("CREATE INDEX inspections_promotion ON inspections(promotion_id,number)")
