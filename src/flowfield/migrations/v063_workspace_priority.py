"""Establish one visible order per workspace column, preserving each project's order."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    rows = db.execute(
        "SELECT project_id,id,row_number() OVER (PARTITION BY workspace_id,"
        "json_extract(data,'$.status') ORDER BY position,project_id,id)-1 "
        "FROM tasks JOIN workspace_projects USING(project_id)"
    ).fetchall()
    db.executemany(
        "UPDATE tasks SET position=? WHERE project_id=? AND id=?",
        [(position, project, task) for project, task, position in rows],
    )
