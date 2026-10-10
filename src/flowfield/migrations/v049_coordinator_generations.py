"""Schema 49: coordinator generations."""

import json
import sqlite3


def apply(db: sqlite3.Connection) -> None:
    old = db.execute(
        "SELECT project_id,harness,session_id,cwd,launch FROM coordinator_sessions"
    ).fetchall()
    db.execute(
        "CREATE TABLE coordinator_session_generations (id TEXT PRIMARY KEY, "
        "project_id TEXT NOT NULL REFERENCES projects(id), data TEXT NOT NULL)"
    )
    db.execute(
        "CREATE INDEX coordinator_generation_project ON coordinator_session_generations(project_id)"
    )
    bindings = []
    for project, harness, session, cwd, launch in old:
        identity, observed_at = db.execute(
            "SELECT lower(hex(randomblob(16))), strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        ).fetchone()
        selected = db.execute(
            "SELECT coalesce(json_extract(data,'$.settings.choice'), "
            "json_extract(data,'$.applied.choice')) "
            "FROM coordinator_turns WHERE project_id=? "
            "AND json_extract(data,'$.settings.choice.harness')=? "
            "AND json_extract(data,'$.session') IN ('new','resumed') "
            "ORDER BY number DESC LIMIT 1",
            (project, harness),
        ).fetchone()
        record = {
            "id": identity,
            "project_id": project,
            "source_id": None,
            "created_at": observed_at,
            "created_by": None,
            "harness": harness,
            "session_id": session,
            "cwd": cwd,
            "launch": json.loads(launch) if launch else None,
            "choice": json.loads(selected[0]) if selected and selected[0] else None,
            "status": "retained",
            "dispatched": None,
            "ephemeral": False,
        }
        db.execute(
            "INSERT INTO coordinator_session_generations VALUES (?,?,?)",
            (identity, project, json.dumps(record, ensure_ascii=False)),
        )
        bindings.append((project, identity))
    db.execute("DROP TABLE coordinator_sessions")
    db.execute(
        "CREATE TABLE coordinator_sessions (project_id TEXT PRIMARY KEY REFERENCES projects(id), "
        "generation_id TEXT REFERENCES coordinator_session_generations(id), "
        "fresh INTEGER NOT NULL DEFAULT 0 CHECK(fresh IN (0,1)))"
    )
    db.executemany("INSERT INTO coordinator_sessions VALUES (?,?,0)", bindings)
    db.execute(
        "ALTER TABLE coordinator_turns ADD COLUMN generation_id TEXT "
        "REFERENCES coordinator_session_generations(id)"
    )
