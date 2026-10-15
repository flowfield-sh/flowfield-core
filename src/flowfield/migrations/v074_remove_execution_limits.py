"""Retire task allowances without changing queue choice or execution ownership."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "UPDATE runs SET data=json_remove(data,'$.active_seconds','$.active_since'), "
        "assignment=json_remove(assignment,'$.execution_limits')"
    )
    db.execute("DROP TABLE task_execution_limits")
    db.execute("DROP TABLE execution_policies")
