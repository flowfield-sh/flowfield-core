"""Durable public replies, independent of the bounded activity presentation."""

import json
import sqlite3
from typing import Any

from flowfield.run_activity import ActivityUpdate, clean


def read(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    row = db.execute("SELECT data FROM coordinator_prose WHERE turn_id=?", (identity,)).fetchone()
    return json.loads(row[0]) if row else {"items": [], "omitted": False}


def write(db: sqlite3.Connection, identity: str, updates: list[ActivityUpdate]) -> None:
    updates = [item for item in updates if item.kind == "agent" or item.key == "stream-gap"]
    if not updates:
        return
    value = read(db, identity)
    for update in updates:
        if update.kind == "agent":
            entry = next((item for item in value["items"] if item["key"] == update.key), None)
            if entry is None:
                entry = {"key": update.key, "text": ""}
                value["items"].append(entry)
            entry["text"] = (entry["text"] if update.append else "") + clean(update.text)
            value["omitted"] |= update.omitted
        elif update.key == "stream-gap":
            value["omitted"] |= update.omitted
    db.execute(
        "INSERT INTO coordinator_prose VALUES (?,?) ON CONFLICT(turn_id) "
        "DO UPDATE SET data=excluded.data",
        (identity, json.dumps(value, ensure_ascii=False)),
    )
