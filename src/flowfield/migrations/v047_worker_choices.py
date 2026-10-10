"""Schema 47: worker choices."""

import json
import sqlite3


def apply(db: sqlite3.Connection) -> None:
    for project, raw in db.execute("SELECT project_id,data FROM worker_settings").fetchall():
        data = json.loads(raw)
        model, effort, mode = (data.pop(key, None) for key in ("model", "effort", "mode"))
        if "selection" not in data:
            data["selection"] = (
                {"harness": "codex", "model": model, "effort": effort, "mode": mode, "fast": False}
                if model and effort
                else None
            )
        db.execute(
            "UPDATE worker_settings SET data=? WHERE project_id=?", (json.dumps(data), project)
        )
