"""Editable workspace classifications; old task revisions remain intact."""

import sqlite3

DEFAULTS = (
    ("feature", "Feature", "blue", "New or expanded product behavior."),
    ("bug", "Bug", "orange", "Correct behavior that does not match the intended outcome."),
    (
        "maintenance",
        "Maintenance",
        "slate",
        "Upkeep of existing code, dependencies or documentation.",
    ),
    ("investigation", "Investigation", "violet", "Answer a question or reduce uncertainty."),
)


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE workspace_labels (workspace_id TEXT NOT NULL REFERENCES workspaces(id), "
        "id TEXT NOT NULL, name TEXT NOT NULL, color TEXT NOT NULL, guidance TEXT NOT NULL, "
        "revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "updated_by TEXT NOT NULL, deleted_at TEXT, PRIMARY KEY(workspace_id,id))"
    )
    db.execute(
        "CREATE UNIQUE INDEX workspace_label_names "
        "ON workspace_labels(workspace_id, name COLLATE NOCASE) "
        "WHERE deleted_at IS NULL"
    )
    for identity, name, color, guidance in DEFAULTS:
        db.execute(
            "INSERT INTO workspace_labels SELECT id,?,?,?,?,1,created_at,created_at,"
            "'migration',NULL FROM workspaces",
            (identity, name, color, guidance),
        )
    # Old task/revision JSON keeps its original classification. The read model maps
    # task_type to its stable default label ID; new revisions use optional label IDs.
    db.execute("UPDATE search_docs SET kind='task' WHERE entity='task'")
    db.execute("DROP TRIGGER search_task_revisions_insert")
    db.execute(
        "CREATE TRIGGER search_task_revisions_insert AFTER INSERT ON task_revisions BEGIN "
        "INSERT INTO search_docs VALUES (new.project_id,'task',new.task_id,new.revision,'task',"
        "new.task_id,json_extract(new.data,'$.updated_at'),json_extract(new.data,'$.title'),"
        "json_extract(new.data,'$.body'),"
        "(SELECT workspace_id FROM workspace_projects WHERE project_id=new.project_id)); END"
    )
