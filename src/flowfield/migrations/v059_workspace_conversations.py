"""Move coordinator evidence and native ownership without merging conversations."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    owners = (
        "SELECT project_id FROM coordinator_conversations UNION "
        "SELECT project_id FROM coordinator_session_generations UNION "
        "SELECT project_id FROM coordinator_sessions UNION "
        "SELECT project_id FROM attachments WHERE task_id IS NULL UNION "
        "SELECT json_extract(data,'$.project_id') FROM harness_catalogs "
        "WHERE json_extract(data,'$.operation')='commands' UNION "
        "SELECT project_id FROM agent_permissions WHERE json_extract(data,'$.role')='coordinator'"
    )
    if db.execute(
        "SELECT 1 FROM (" + owners + ") old WHERE NOT EXISTS "
        "(SELECT 1 FROM workspace_projects p WHERE p.project_id=old.project_id) LIMIT 1"
    ).fetchone():
        raise ValueError("Coordinator evidence has no workspace membership")
    if db.execute(
        "SELECT 1 FROM coordinator_conversations c JOIN workspace_projects p "
        "ON p.project_id=c.project_id GROUP BY p.workspace_id "
        "HAVING count(DISTINCT c.project_id)>1"
    ).fetchone():
        raise ValueError("Cannot merge separate project coordinator conversations")
    if db.execute(
        "SELECT 1 FROM coordinator_sessions s JOIN workspace_projects p "
        "ON p.project_id=s.project_id WHERE s.generation_id IS NOT NULL "
        "GROUP BY p.workspace_id HAVING count(*)>1"
    ).fetchone():
        raise ValueError("Cannot merge separate native coordinator bindings")

    db.execute(
        "UPDATE harness_catalogs SET data=json_set(data,'$.project_id',NULL,'$.workspace_id',"
        "(SELECT workspace_id FROM workspace_projects WHERE project_id="
        "json_extract(harness_catalogs.data,'$.project_id'))) "
        "WHERE json_extract(data,'$.operation')='commands'"
    )

    # Copy the complete reference graph inside the existing upgrade transaction,
    # then drop children before parents. Foreign-key enforcement stays enabled;
    # no deferred-constraint bypass or change to the storage/snapshot owner is needed.
    tables = (
        "coordinator_handoffs",
        "coordinator_prose",
        "coordinator_turns",
        "coordinator_sessions",
        "coordinator_conversations",
        "coordinator_session_generations",
        "attachments",
        "agent_permissions",
    )
    sequences = dict(
        db.execute(
            "SELECT name,seq FROM sqlite_sequence WHERE name IN "
            "('coordinator_turns','coordinator_conversations','agent_permissions')"
        )
    )
    for table in tables:
        db.execute(f"CREATE TEMP TABLE old_{table} AS SELECT * FROM {table}")
    for table in tables:
        db.execute(f"DROP TABLE {table}")

    db.execute(
        "CREATE TABLE coordinator_session_generations (id TEXT PRIMARY KEY, "
        "workspace_id TEXT NOT NULL REFERENCES workspaces(id), data TEXT NOT NULL)"
    )
    db.execute(
        "CREATE INDEX coordinator_generation_workspace ON "
        "coordinator_session_generations(workspace_id)"
    )
    db.execute(
        "CREATE TABLE coordinator_conversations (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), "
        "created_at TEXT NOT NULL, welcome TEXT NOT NULL DEFAULT '')"
    )
    db.execute(
        "CREATE INDEX coordinator_workspaces ON coordinator_conversations(workspace_id,number)"
    )
    db.execute(
        "CREATE TABLE coordinator_sessions (workspace_id TEXT PRIMARY KEY "
        "REFERENCES workspaces(id), "
        "generation_id TEXT REFERENCES coordinator_session_generations(id), "
        "fresh INTEGER NOT NULL DEFAULT 0 CHECK(fresh IN (0,1)))"
    )
    db.execute(
        "CREATE TABLE coordinator_turns (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), "
        "conversation_id TEXT NOT NULL REFERENCES coordinator_conversations(id), "
        "status TEXT NOT NULL, data TEXT NOT NULL, "
        "generation_id TEXT REFERENCES coordinator_session_generations(id))"
    )
    db.execute("CREATE INDEX coordinator_history ON coordinator_turns(conversation_id,number)")
    db.execute(
        "CREATE UNIQUE INDEX coordinator_active ON coordinator_turns(workspace_id) "
        "WHERE status IN ('starting','running','stopping','uncertain')"
    )
    db.execute(
        "CREATE INDEX coordinator_workspace_messages ON coordinator_turns(workspace_id,number)"
    )
    for table in ("coordinator_handoffs", "coordinator_prose"):
        db.execute(
            f"CREATE TABLE {table} (turn_id TEXT PRIMARY KEY REFERENCES coordinator_turns(id), "
            "data TEXT NOT NULL)"
        )
    db.execute(
        "INSERT INTO coordinator_session_generations SELECT o.id,p.workspace_id,"
        "json_set(json_remove(o.data,'$.project_id'),'$.workspace_id',p.workspace_id) "
        "FROM old_coordinator_session_generations o JOIN workspace_projects p "
        "ON p.project_id=o.project_id"
    )
    db.execute(
        "INSERT INTO coordinator_conversations SELECT "
        "o.number,o.id,p.workspace_id,o.created_at,o.welcome "
        "FROM old_coordinator_conversations o JOIN workspace_projects p "
        "ON p.project_id=o.project_id"
    )
    db.execute(
        "INSERT INTO coordinator_sessions SELECT p.workspace_id,max(o.generation_id),1 "
        "FROM old_coordinator_sessions o JOIN workspace_projects p ON p.project_id=o.project_id "
        "GROUP BY p.workspace_id"
    )
    db.execute(
        "INSERT INTO coordinator_turns SELECT "
        "o.number,o.id,p.workspace_id,o.conversation_id,o.status,"
        "json_set(json_remove(o.data,'$.project_id'),'$.workspace_id',p.workspace_id,'$.task_context',"
        "json(CASE WHEN json_type(o.data,'$.task_context')='object' THEN "
        "json_set(json_extract(o.data,'$.task_context'),'$.project_id',o.project_id) "
        "ELSE 'null' END)),"
        "o.generation_id FROM old_coordinator_turns o JOIN "
        "workspace_projects p ON p.project_id=o.project_id"
    )
    for table in ("coordinator_handoffs", "coordinator_prose"):
        db.execute(f"INSERT INTO {table} SELECT * FROM old_{table}")

    db.execute(
        "CREATE TABLE attachments (id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), "
        "task_id TEXT, name TEXT NOT NULL, mime TEXT NOT NULL, created_at TEXT NOT NULL, "
        "content BLOB NOT NULL, size INTEGER NOT NULL, bound INTEGER NOT NULL DEFAULT 0, "
        "workspace_id TEXT REFERENCES workspaces(id), legacy_project_id "
        "TEXT REFERENCES projects(id), "
        "FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id), "
        "CHECK((workspace_id IS NOT NULL AND project_id IS NULL AND task_id IS NULL) OR "
        "(workspace_id IS NULL AND project_id IS NOT NULL AND task_id IS "
        "NOT NULL AND legacy_project_id IS NULL)))"
    )
    db.execute("CREATE INDEX attachment_project ON attachments(project_id,task_id)")
    db.execute("CREATE INDEX attachment_workspace ON attachments(workspace_id)")
    db.execute(
        "INSERT INTO attachments SELECT o.id,CASE WHEN o.task_id IS NOT NULL THEN o.project_id END,"
        "o.task_id,o.name,o.mime,o.created_at,o.content,o.size,o.bound,"
        "CASE WHEN o.task_id IS NULL THEN p.workspace_id END,"
        "CASE WHEN o.task_id IS NULL THEN o.project_id END "
        "FROM old_attachments o LEFT JOIN workspace_projects p ON p.project_id=o.project_id"
    )
    db.execute(
        "CREATE TABLE agent_permissions (number INTEGER PRIMARY KEY "
        "AUTOINCREMENT, id TEXT NOT NULL UNIQUE, "
        "project_id TEXT REFERENCES projects(id), task_id TEXT, binding "
        "TEXT NOT NULL, status TEXT NOT NULL, "
        "data TEXT NOT NULL, workspace_id TEXT REFERENCES workspaces(id), "
        "FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id), "
        "CHECK((workspace_id IS NOT NULL AND project_id IS NULL AND task_id IS NULL "
        "AND json_extract(data,'$.role')='coordinator') OR (workspace_id "
        "IS NULL AND project_id IS NOT NULL "
        "AND json_extract(data,'$.role')='worker')))"
    )
    db.execute("CREATE INDEX permissions_project ON agent_permissions(project_id,task_id,number)")
    db.execute("CREATE INDEX permissions_workspace ON agent_permissions(workspace_id,number)")
    db.execute("CREATE INDEX permissions_binding ON agent_permissions(binding,status)")
    db.execute(
        "INSERT INTO agent_permissions SELECT o.number,o.id,"
        "CASE WHEN json_extract(o.data,'$.role')='worker' THEN o.project_id END,"
        "o.task_id,o.binding,o.status,CASE WHEN json_extract(o.data,'$.role')='coordinator' THEN "
        "json_set(json_remove(o.data,'$.project_id'),'$.workspace_id',p.workspace_id) "
        "ELSE o.data END,"
        "CASE WHEN json_extract(o.data,'$.role')='coordinator' THEN p.workspace_id END "
        "FROM old_agent_permissions o LEFT JOIN workspace_projects p ON p.project_id=o.project_id"
    )
    for table, sequence in sequences.items():
        db.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
        db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", (table, sequence))
    for table in tables:
        db.execute(f"DROP TABLE old_{table}")
