"""Schema 57: retain native sources while moving coordinators to managed homes."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    # Native sessions bind their cwd. Preserve their identities/evidence as sources,
    # but start a fresh generation on the next authorized message. Active/uncertain
    # turns retain their ownership and must still pass ordinary restart/cleanup.
    db.execute(
        "UPDATE coordinator_sessions SET fresh=1 WHERE project_id IN "
        "(SELECT project_id FROM workspace_projects)"
    )
