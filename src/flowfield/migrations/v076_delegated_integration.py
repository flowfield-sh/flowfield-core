"""Human-granted milestone integration policy history; existing grants default off."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE milestone_integration_policy_history (
        delivery_id TEXT NOT NULL REFERENCES milestone_deliveries(id),
        revision INTEGER NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY(delivery_id, revision)
    )""")
