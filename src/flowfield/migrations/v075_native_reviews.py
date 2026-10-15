"""Native review attempts retain lifecycle receipts without scheduling historical results."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE review_attempts (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, review_id TEXT NOT NULL REFERENCES task_reviews(id), "
        "data TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX review_attempt_owner ON review_attempts(review_id,number)")
