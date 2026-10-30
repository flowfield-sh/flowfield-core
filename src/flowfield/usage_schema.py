"""Additive usage storage for published schemas 86/87/89 -> 90; no native-session backfill."""

TABLES = """
CREATE TABLE usage_metadata (
 id INTEGER PRIMARY KEY CHECK(id=1), collection_started_at TEXT NOT NULL);
INSERT INTO usage_metadata VALUES(1, strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
CREATE TABLE usage_executions (
 number INTEGER PRIMARY KEY AUTOINCREMENT,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 source TEXT NOT NULL, source_id TEXT NOT NULL,
 task_id TEXT, related_task_id TEXT, project_id TEXT, parent_id TEXT, subtask_id TEXT,
 role TEXT NOT NULL, purpose TEXT NOT NULL, outcome TEXT NOT NULL,
 harness TEXT, requested_model TEXT, session_id TEXT,
 created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT,
 dispatch_count INTEGER NOT NULL DEFAULT 0,
 timing TEXT NOT NULL DEFAULT 'not-dispatched', legacy INTEGER NOT NULL DEFAULT 0,
 reasons TEXT NOT NULL DEFAULT '[]',
 UNIQUE(source,source_id),
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id) ON DELETE CASCADE
);
CREATE INDEX usage_workspace ON usage_executions(workspace_id,number);
CREATE INDEX usage_task ON usage_executions(workspace_id,task_id,number);
CREATE TABLE usage_observations (
 number INTEGER PRIMARY KEY AUTOINCREMENT,
 execution INTEGER NOT NULL REFERENCES usage_executions(number) ON DELETE CASCADE,
 series TEXT NOT NULL, event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 received_at TEXT NOT NULL, data TEXT NOT NULL, baseline TEXT,
 delta TEXT NOT NULL, disposition TEXT NOT NULL, cost TEXT,
 UNIQUE(execution,series,event_id,fingerprint)
);
CREATE INDEX usage_receipt ON usage_observations(received_at,execution);
CREATE INDEX usage_evidence ON usage_observations(execution,number);
CREATE TABLE usage_closed_scopes (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), series TEXT NOT NULL,
 PRIMARY KEY(workspace_id,series)
);
CREATE TRIGGER usage_execution_scope_delete BEFORE DELETE ON usage_executions BEGIN
 INSERT OR IGNORE INTO usage_closed_scopes(workspace_id,series)
 SELECT old.workspace_id,series FROM usage_observations WHERE execution=old.number;
END;
CREATE TABLE usage_checkpoints (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), series TEXT NOT NULL,
 execution INTEGER NOT NULL REFERENCES usage_executions(number) ON DELETE CASCADE,
 data TEXT NOT NULL, PRIMARY KEY(workspace_id,series)
);
"""

# One row per existing owner, never per result/reference. Source deletion removes
# accounting atomically; archive does not. Coordinator task context is only related.
SOURCES = {
    "run": (
        "runs",
        "r.workspace_id",
        "r.task_id",
        "NULL",
        "json_extract(r.data,'$.project_id')",
        "NULL",
        "r.subtask_id",
        "'worker'",
        "coalesce(json_extract(r.data,'$.launch_reason'),'unknown')",
        "r.status",
        "json_extract(r.data,'$.agent_settings.choice.harness')",
        "json_extract(r.data,'$.model')",
        "r",
        "coalesce(json_extract(r.data,'$.purpose'),'work') != 'assembly'",
    ),
    "review_attempt": (
        "review_attempts",
        "j.workspace_id",
        "j.task_id",
        "NULL",
        "(SELECT project_id FROM tasks WHERE workspace_id=j.workspace_id AND id=j.task_id)",
        "r.review_id",
        "json_extract(j.data,'$.responding_subtask_id')",
        "CASE json_extract(r.data,'$.role') WHEN 'implementer' THEN 'worker' ELSE 'reviewer' END",
        "coalesce(json_extract(r.data,'$.launch_reason'),'unknown')",
        "coalesce(json_extract(r.data,'$.status'),'unknown')",
        "json_extract(r.data,'$.requested_agent.harness')",
        "json_extract(r.data,'$.requested_agent.model')",
        "r JOIN task_reviews j ON j.id=r.review_id",
        "1",
    ),
    "coordinator_turn": (
        "coordinator_turns",
        "r.workspace_id",
        "NULL",
        "json_extract(r.data,'$.task_context.task_id')",
        "NULL",
        "r.conversation_id",
        "NULL",
        "'coordinator'",
        "'message'",
        "r.status",
        "json_extract(r.data,'$.settings.choice.harness')",
        "json_extract(r.data,'$.settings.choice.model')",
        "r",
        "1",
    ),
}


def insertion(source: str, *, legacy: bool, where: str = "") -> str:
    (
        table,
        workspace,
        task,
        related,
        project,
        parent,
        subtask,
        role,
        purpose,
        outcome,
        harness,
        model,
        join,
        predicate,
    ) = SOURCES[source]
    return f"""INSERT OR IGNORE INTO usage_executions
    (workspace_id,source,source_id,task_id,related_task_id,project_id,parent_id,subtask_id,
    role,purpose,outcome,harness,requested_model,created_at,legacy,timing,reasons)
    SELECT {workspace},'{source}',r.id,{task},{related},{project},{parent},{subtask},
    {role},{purpose},{outcome},{harness},{model},json_extract(r.data,'$.created_at'),
    {int(legacy)},'{"legacy" if legacy else "not-dispatched"}',
    '{'["legacy-no-consumption"]' if legacy else "[]"}'
    FROM {table} {join} WHERE ({predicate}) {where};"""


TRIGGERS = ""
for source, spec in SOURCES.items():
    table = spec[0]
    TRIGGERS += f"""
    CREATE TRIGGER usage_{source}_insert AFTER INSERT ON {table} BEGIN
    {insertion(source, legacy=False, where="AND r.id=new.id")}
    END;
    CREATE TRIGGER usage_{source}_delete AFTER DELETE ON {table} BEGIN
    DELETE FROM usage_executions WHERE source='{source}' AND source_id=old.id;
    END;
    """
    outcome = (
        "new.status"
        if source != "review_attempt"
        else "coalesce(json_extract(new.data,'$.status'),'unknown')"
    )
    harness = (
        "json_extract(new.data,'$.requested_agent.harness')"
        if source == "review_attempt"
        else "NULL"
    )
    model = (
        "json_extract(new.data,'$.requested_agent.model')" if source == "review_attempt" else "NULL"
    )
    TRIGGERS += f"""
    CREATE TRIGGER usage_{source}_update AFTER UPDATE ON {table} BEGIN
    UPDATE usage_executions SET outcome={outcome},
    harness=coalesce({harness},harness), requested_model=coalesce({model},requested_model)
    WHERE source='{source}' AND source_id=new.id;
    END;
    """
TRIGGERS += """
CREATE TRIGGER usage_related_delete AFTER DELETE ON tasks BEGIN
UPDATE usage_executions SET related_task_id=NULL
WHERE workspace_id=old.workspace_id AND related_task_id=old.id;
END;
"""

BACKFILL = "".join(insertion(source, legacy=True) for source in SOURCES)
# Retained Worker timestamps bound service execution including cleanup, not native
# dispatch. Preserve them with an explicit legacy timing label; other ends unknown.
BACKFILL += """
UPDATE usage_executions SET
 started_at=(SELECT json_extract(data,'$.started_at') FROM runs WHERE id=source_id),
 ended_at=(SELECT json_extract(data,'$.ended_at') FROM runs WHERE id=source_id)
 WHERE source='run';
UPDATE usage_executions SET
 started_at=(SELECT json_extract(data,'$.created_at') FROM review_attempts WHERE id=source_id),
 ended_at=(SELECT json_extract(data,'$.completed_at') FROM review_attempts WHERE id=source_id)
 WHERE source='review_attempt';
"""
