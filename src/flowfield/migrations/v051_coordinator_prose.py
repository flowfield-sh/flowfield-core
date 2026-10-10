"""Schema 51: coordinator prose."""

import json
import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE coordinator_prose (turn_id TEXT PRIMARY KEY "
        "REFERENCES coordinator_turns(id), data TEXT NOT NULL)"
    )
    for identity, raw in db.execute(
        "SELECT id,json_extract(data,'$.activity') FROM coordinator_turns"
    ):
        activity = json.loads(raw)
        value = {
            "items": [
                {"key": entry["key"], "text": entry["text"]}
                for entry in activity["items"]
                if entry["kind"] == "agent"
            ],
            "omitted": bool(
                activity.get("omitted")
                or any(
                    entry.get("omitted")
                    for entry in activity["items"]
                    if entry["kind"] == "agent" or entry["key"] == "stream-gap"
                )
            ),
        }
        db.execute(
            "INSERT INTO coordinator_prose VALUES (?,?)",
            (identity, json.dumps(value, ensure_ascii=False)),
        )
