"""Schema 48: coordinator handoffs."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    # Past native prompts cannot be reconstructed honestly. Only new accepted
    # messages acquire snapshots; interrupted prompts are never replayed.
    db.execute(
        "CREATE TABLE coordinator_handoffs (turn_id TEXT PRIMARY KEY "
        "REFERENCES coordinator_turns(id), data TEXT NOT NULL)"
    )
