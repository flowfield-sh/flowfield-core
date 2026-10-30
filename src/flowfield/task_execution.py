"""Current execution ownership shared by task controls and the stop transaction."""

import sqlite3


def task_edit_blocker(db: sqlite3.Connection, workspace_id: str, task_id: str) -> str | None:
    if (
        any(active_task_execution(db, workspace_id, task_id))
        or db.execute(
            "SELECT 1 FROM result_versions WHERE workspace_id=? AND task_id=? "
            "AND status IN ('preparing','delivering')",
            (workspace_id, task_id),
        ).fetchone()
        or db.execute(
            "SELECT 1 FROM task_replies WHERE workspace_id=? AND task_id=? "
            "AND json_extract(data,'$.status')='pending'",
            (workspace_id, task_id),
        ).fetchone()
    ):
        return (
            "Flowfield is working on this task. "
            "Stop it or wait for the work to finish before editing."
        )
    return None


def active_task_execution(
    db: sqlite3.Connection, workspace_id: str, task_id: str
) -> tuple[list[str], list[str]]:
    runs = [
        row[0]
        for row in db.execute(
            "SELECT id FROM runs WHERE workspace_id=? AND task_id=? "
            "AND status IN ('preparing','running','stopping','uncertain')",
            (workspace_id, task_id),
        )
    ]
    reviews = [
        row[0]
        for row in db.execute(
            "SELECT id FROM task_reviews WHERE workspace_id=? AND task_id=? "
            "AND status IN ('preparing','running','uncertain')",
            (workspace_id, task_id),
        )
    ]
    return runs, reviews
