"""Preserve published result recipes and add independently owned run output files."""

import json
import sqlite3


def migrate(db: sqlite3.Connection) -> None:
    if "inspection_id" not in {row[1] for row in db.execute("PRAGMA table_info(output_files)")}:
        sequence = db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='output_files'"
        ).fetchone()
        db.execute("""CREATE TABLE runtime_output_files (
            number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id), run_id TEXT REFERENCES runs(id),
            data TEXT NOT NULL, review_attempt_id TEXT REFERENCES review_attempts(id),
            inspection_id TEXT REFERENCES inspections(id),
            CHECK ((run_id IS NOT NULL) + (review_attempt_id IS NOT NULL) +
                (inspection_id IS NOT NULL) = 1))""")
        db.execute("INSERT INTO runtime_output_files SELECT *,NULL FROM output_files")
        db.execute("DROP TABLE output_files")
        db.execute("ALTER TABLE runtime_output_files RENAME TO output_files")
        db.execute("CREATE INDEX output_run ON output_files(run_id,number)")
        db.execute("CREATE INDEX output_workspace ON output_files(workspace_id,number)")
        if sequence:
            db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='output_files'", (sequence[0],))
        else:
            db.execute("DELETE FROM sqlite_sequence WHERE name='output_files'")
    # Preserve the instructions of an already prepared copy. Otherwise use the frozen
    # assignment; never invent historical recipe evidence from today's project defaults.
    for identity, data, assignment, run_data in db.execute(
        "SELECT v.id,v.data,r.assignment,r.data FROM result_versions v JOIN runs r ON r.id=v.run_id"
    ).fetchall():
        value = json.loads(data)
        if value.get("completion") != "code" or value.get("recipe") is not None:
            continue
        run = json.loads(run_data)
        sections = json.loads(assignment)
        recipe = json.loads(sections.get("preview", "{}"))
        recipe.update(
            setup_commands=run.get("setup_commands", []),
            setup_timeout_seconds=run.get("setup_timeout_seconds", 120),
            run_timeout_seconds=run.get("check_timeout_seconds", 60),
        )
        copy = db.execute(
            "SELECT data FROM inspections WHERE result_id=? ORDER BY number DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if copy:
            prepared = json.loads(copy[0])
            for field in ("setup_commands", "run_command", "ready_command", "url"):
                recipe[field] = prepared.get(field, [] if field == "setup_commands" else "")
            if recipe.get("run_command"):
                recipe["no_preview_reason"] = ""
        value["recipe"] = recipe
        db.execute("UPDATE result_versions SET data=? WHERE id=?", (json.dumps(value), identity))
