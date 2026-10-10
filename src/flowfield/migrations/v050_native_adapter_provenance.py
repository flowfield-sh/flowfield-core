"""Schema 50: native adapter provenance."""

import json
import sqlite3


def apply(db: sqlite3.Connection) -> None:
    # Retain old versions so native continuity requires explicit revalidation.
    def update(value: object) -> object:
        if isinstance(value, list):
            return [update(item) for item in value]
        if isinstance(value, dict):
            result = {key: update(item) for key, item in value.items()}
            if "registration_revision" in result and "native_executable" in result:
                if "bridge_executable" in result:
                    result["runtime_executable"] = result.pop("bridge_executable")
                if "bridge_version" in result:
                    result["adapter_version"] = result.pop("bridge_version")
            return result
        return value

    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
        if "data" not in columns:
            continue
        for identity, raw in db.execute(f'SELECT rowid,data FROM "{table}"').fetchall():
            value = json.loads(raw)
            changed = update(value)
            if value != changed:
                db.execute(
                    f'UPDATE "{table}" SET data=? WHERE rowid=?', (json.dumps(changed), identity)
                )
