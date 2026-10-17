"""Workspace-owned work shares task lifecycle without registering a project.

The existing project_id columns on execution records retain their opaque owner keys.
Their foreign keys now identify work scopes; repository registrations stay separate.
Copy the complete affected reference graph with foreign keys enforced throughout.
"""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE work_scopes (id TEXT PRIMARY KEY, "
        "project_id TEXT UNIQUE REFERENCES projects(id), "
        "workspace_id TEXT REFERENCES workspaces(id), "
        "CHECK(project_id IS NOT NULL OR workspace_id IS NOT NULL))"
    )
    db.execute(
        "INSERT INTO work_scopes SELECT p.id,p.id,m.workspace_id FROM projects p "
        "LEFT JOIN workspace_projects m ON m.project_id=p.id"
    )
    db.execute("INSERT INTO work_scopes SELECT 'workspace_' || id,NULL,id FROM workspaces")
    db.execute(
        "CREATE UNIQUE INDEX workspace_work_scope ON work_scopes(workspace_id) "
        "WHERE project_id IS NULL"
    )
    # Parent registrations are unchanged. Everything else may reference a task owner;
    # repository-only operations still validate actual projects at their boundary.
    schemas = dict(
        db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    foreign = {
        table: {row[2] for row in db.execute(f'PRAGMA foreign_key_list("{table}")')}
        for table in schemas
    }
    affected = {
        table
        for table, parents in foreign.items()
        if "projects" in parents and table not in {"work_scopes", "workspace_projects"}
    }
    while expanded := {
        table for table, parents in foreign.items() if parents & affected and table not in affected
    }:
        affected.update(expanded)
    ancillary = list(
        db.execute(
            "SELECT name,type,tbl_name,sql FROM sqlite_master "
            "WHERE type IN ('index','trigger','view') AND sql IS NOT NULL"
        )
    )
    sequences = dict(db.execute("SELECT name,seq FROM sqlite_sequence"))
    for table in affected:
        db.execute(f'CREATE TEMP TABLE "saved_{table}" AS SELECT * FROM "{table}"')
    for name, kind, _, _ in ancillary:
        if kind == "view":
            db.execute(f'DROP VIEW "{name}"')
    remaining = set(affected)
    dropped: list[str] = []
    while remaining:
        leaves = sorted(
            table
            for table in remaining
            if not any(table in foreign[child] for child in remaining - {table})
        )
        if not leaves:
            raise ValueError("Cannot migrate a cyclic work ownership reference graph")
        for table in leaves:
            db.execute(f'DROP TABLE "{table}"')
            dropped.append(table)
            remaining.remove(table)
    for table in reversed(dropped):
        definition = schemas[table].replace("REFERENCES projects(id)", "REFERENCES work_scopes(id)")
        if table == "tasks":
            definition = definition.replace("key TEXT NOT NULL UNIQUE", "key TEXT NOT NULL")
        db.execute(definition)
        db.execute(f'INSERT INTO "{table}" SELECT * FROM "saved_{table}"')
        db.execute(f'DROP TABLE "saved_{table}"')
        if table in sequences:
            db.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
            db.execute("INSERT INTO sqlite_sequence VALUES (?,?)", (table, sequences[table]))
    for _, kind, table, sql in ancillary:
        if kind == "view" or table in affected:
            db.execute(sql)
    db.execute("CREATE UNIQUE INDEX task_scope_key ON tasks(project_id,key)")
    db.execute(
        "CREATE VIEW workspace_scopes AS SELECT id AS project_id, workspace_id "
        "FROM work_scopes WHERE workspace_id IS NOT NULL"
    )
    db.execute(
        "CREATE TRIGGER project_work_scope AFTER INSERT ON projects BEGIN "
        "INSERT INTO work_scopes(id,project_id) VALUES (NEW.id,NEW.id); END"
    )
    db.execute(
        "CREATE TRIGGER project_scope_membership AFTER INSERT ON workspace_projects BEGIN "
        "UPDATE work_scopes SET workspace_id=NEW.workspace_id WHERE project_id=NEW.project_id; END"
    )
    db.execute(
        "CREATE TRIGGER workspace_work_scope AFTER INSERT ON workspaces BEGIN "
        "INSERT INTO work_scopes VALUES ('workspace_' || NEW.id,NULL,NEW.id); END"
    )
