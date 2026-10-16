"""Prepared verification plans and native milestone review ownership."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE milestone_review_plans (
        number INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT NOT NULL, milestone_id TEXT NOT NULL,
        revision INTEGER NOT NULL, data TEXT NOT NULL,
        UNIQUE(workspace_id, milestone_id, revision),
        FOREIGN KEY(workspace_id, milestone_id) REFERENCES milestones(workspace_id, id)
    )""")
    db.execute("""CREATE TABLE milestone_reviews (
        number INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        project_id TEXT NOT NULL REFERENCES projects(id),
        verification_id TEXT NOT NULL UNIQUE REFERENCES milestone_verifications(id),
        status TEXT NOT NULL, data TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX milestone_review_work ON milestone_reviews(status, number)")
    db.execute("""CREATE TABLE milestone_review_attempts (
        number INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        review_id TEXT NOT NULL REFERENCES milestone_reviews(id), data TEXT NOT NULL
    )""")
    db.execute(
        "CREATE INDEX milestone_review_rounds ON milestone_review_attempts(review_id, number)"
    )
