"""Preserve existing output identities while adding review-owned evidence."""

import sqlite3


def migrate_outputs(db: sqlite3.Connection) -> None:
    # Idempotence also supports published-baseline fixtures carrying additive columns.
    if "review_attempt_id" in {row[1] for row in db.execute("PRAGMA table_info(output_files)")}:
        return
    sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='output_files'").fetchone()
    db.execute("""CREATE TABLE updated_output_files (
        number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        run_id TEXT REFERENCES runs(id), data TEXT NOT NULL,
        review_attempt_id TEXT REFERENCES review_attempts(id),
        CHECK ((run_id IS NOT NULL) != (review_attempt_id IS NOT NULL)))""")
    db.execute("INSERT INTO updated_output_files SELECT *,NULL FROM output_files")
    db.execute("DROP TABLE output_files")
    db.execute("ALTER TABLE updated_output_files RENAME TO output_files")
    db.execute("CREATE INDEX output_run ON output_files(run_id,number)")
    db.execute("CREATE INDEX output_workspace ON output_files(workspace_id,number)")
    if sequence:
        db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='output_files'", (sequence[0],))
