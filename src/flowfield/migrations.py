"""Ordered database-only upgrades from the Flowfield schema-44 baseline."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

BASELINE_VERSION = 44


@dataclass(frozen=True)
class Migration:
    version: int
    apply: Callable[[sqlite3.Connection], None]


def host_harnesses(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE harness_settings (harness TEXT PRIMARY KEY "
        "CHECK(harness IN ('codex','claude-code')), revision INTEGER NOT NULL "
        "CHECK(revision>=1), executable TEXT, config_directory TEXT)"
    )
    db.executemany(
        "INSERT INTO harness_settings VALUES (?,1,NULL,NULL)", [("codex",), ("claude-code",)]
    )
    db.execute("ALTER TABLE coordinator_sessions ADD COLUMN launch TEXT")


def harness_catalogs(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE harness_catalogs (harness TEXT PRIMARY KEY "
        "CHECK(harness IN ('codex','claude-code')), data TEXT NOT NULL)"
    )


def worker_choices(db: sqlite3.Connection) -> None:
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


def coordinator_handoffs(db: sqlite3.Connection) -> None:
    # Past native prompts cannot be reconstructed honestly. Only new accepted
    # messages acquire snapshots; interrupted prompts are never replayed.
    db.execute(
        "CREATE TABLE coordinator_handoffs (turn_id TEXT PRIMARY KEY "
        "REFERENCES coordinator_turns(id), data TEXT NOT NULL)"
    )


def coordinator_generations(db: sqlite3.Connection) -> None:
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


def native_adapter_provenance(db: sqlite3.Connection) -> None:
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


def coordinator_prose(db: sqlite3.Connection) -> None:
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


MIGRATIONS: tuple[Migration, ...] = (
    Migration(45, host_harnesses),
    Migration(46, harness_catalogs),
    Migration(47, worker_choices),
    Migration(48, coordinator_handoffs),
    Migration(49, coordinator_generations),
    Migration(50, native_adapter_provenance),
    Migration(51, coordinator_prose),
)


def current_version() -> int:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions))):
        raise RuntimeError("Database migrations must be contiguous and ordered.")
    return versions[-1] if versions else BASELINE_VERSION
