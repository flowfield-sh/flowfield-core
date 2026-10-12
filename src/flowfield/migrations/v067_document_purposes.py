"""Make live selection purposes explicit; preserve all frozen assignment/history bytes."""

import json
import sqlite3


def apply(db: sqlite3.Connection) -> None:
    for project_id, saved in db.execute("SELECT id,worker_documents FROM projects").fetchall():
        selections = json.loads(saved)
        for item in selections:
            item.setdefault("purpose", "required")
        db.execute(
            "UPDATE projects SET worker_documents=? WHERE id=?",
            (json.dumps(selections), project_id),
        )
    for project_id, task_id, saved in db.execute("SELECT project_id,id,data FROM tasks").fetchall():
        data = json.loads(saved)
        if data.get("documents"):
            for item in data["documents"]:
                item.setdefault("purpose", "required")
            db.execute(
                "UPDATE tasks SET data=? WHERE project_id=? AND id=?",
                (json.dumps(data), project_id, task_id),
            )
