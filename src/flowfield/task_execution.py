"""Current execution ownership shared by task controls and the stop transaction."""

import sqlite3


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
