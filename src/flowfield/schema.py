"""Current schema and additive migration definitions for published stores."""

from flowfield.usage_schema import TABLES, TRIGGERS

SCHEMA_VERSION = 90

TASK_REVIEWS = """
CREATE TABLE task_reviews (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL
    UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), task_id TEXT NOT
    NULL, result_id TEXT REFERENCES result_versions(id), binding TEXT NOT
    NULL, status TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(result_id, binding), FOREIGN
    KEY(workspace_id, task_id) REFERENCES tasks(workspace_id, id))
"""

TASK_DELETIONS = """
CREATE TABLE task_deletions (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), task_id TEXT NOT NULL,
 key TEXT NOT NULL, number INTEGER NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(workspace_id,task_id), UNIQUE(workspace_id,key), UNIQUE(workspace_id,number)
)
"""

SCHEMA = """
CREATE TABLE activity (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, workspace_id TEXT
    NOT NULL REFERENCES workspaces(id), task_id TEXT, kind TEXT NOT NULL CHECK(kind
    IN ('note', 'handoff', 'event')), body TEXT NOT NULL, author TEXT NOT NULL, created_at
    TEXT NOT NULL, supersedes TEXT UNIQUE REFERENCES activity(id), task_revision INTEGER,
    question_id TEXT, FOREIGN KEY (workspace_id, task_id) REFERENCES tasks(workspace_id,
    id)
);

CREATE TABLE agent_permissions (
 number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), task_id TEXT,
 binding TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id)
);

CREATE TABLE agent_settings (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    role TEXT NOT NULL CHECK(role IN ('worker', 'coordinator')), scope TEXT NOT NULL,
    revision INTEGER NOT NULL, selection TEXT, PRIMARY KEY(workspace_id, role, scope));

CREATE TABLE announcement_clients (id TEXT PRIMARY KEY, seen_at REAL NOT NULL, joined_at
    REAL NOT NULL, active_since REAL NOT NULL, data TEXT NOT NULL);

CREATE TABLE announcement_events (id INTEGER PRIMARY KEY AUTOINCREMENT, source_key
    TEXT NOT NULL, workspace_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL, created_at
    REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', client_id TEXT, channel
    TEXT);

CREATE TABLE announcement_preferences (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT
    NOT NULL);

CREATE TABLE attachments (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), task_id TEXT,
 name TEXT NOT NULL, mime TEXT NOT NULL, created_at TEXT NOT NULL, content BLOB NOT NULL,
 size INTEGER NOT NULL, bound INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id)
);

CREATE TABLE attention_sources (key TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, signature
    TEXT NOT NULL);

CREATE TABLE coordinator_conversations (number INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    created_at TEXT NOT NULL, welcome TEXT NOT NULL DEFAULT '');

CREATE TABLE coordinator_handoffs (turn_id TEXT PRIMARY KEY REFERENCES coordinator_turns(id),
    data TEXT NOT NULL);

CREATE TABLE coordinator_prose (turn_id TEXT PRIMARY KEY REFERENCES coordinator_turns(id),
    data TEXT NOT NULL);

CREATE TABLE coordinator_session_generations (id TEXT PRIMARY KEY, workspace_id TEXT
    NOT NULL REFERENCES workspaces(id), data TEXT NOT NULL);

CREATE TABLE coordinator_sessions (workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
    generation_id TEXT REFERENCES coordinator_session_generations(id), fresh INTEGER
    NOT NULL DEFAULT 0 CHECK(fresh IN (0, 1)));

CREATE TABLE coordinator_turns (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT
    NOT NULL UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), conversation_id
    TEXT NOT NULL REFERENCES coordinator_conversations(id), status TEXT NOT NULL,
    data TEXT NOT NULL, generation_id TEXT REFERENCES coordinator_session_generations(id));

CREATE TABLE execution_local (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), data TEXT NOT NULL
);

CREATE TABLE harness_catalogs (harness TEXT PRIMARY KEY CHECK(harness IN ('codex',
    'claude-code', 'pi')), data TEXT NOT NULL);

CREATE TABLE harness_settings (harness TEXT PRIMARY KEY CHECK(harness IN ('codex',
    'claude-code', 'pi')), revision INTEGER NOT NULL CHECK(revision>=1), executable
    TEXT, config_directory TEXT);

CREATE TABLE inspection_settings (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), data TEXT NOT NULL
);

CREATE TABLE inspections (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, project_id TEXT
    NOT NULL REFERENCES projects(id), result_id TEXT REFERENCES result_versions(id),
    data TEXT NOT NULL
);

CREATE TABLE integration_settings (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), data TEXT NOT NULL
);

CREATE TABLE integrations (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, project_id TEXT
    NOT NULL REFERENCES projects(id), run_id TEXT NOT NULL REFERENCES runs(id),
    status TEXT NOT NULL, data TEXT NOT NULL
);










CREATE TABLE output_files (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL
    UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), run_id TEXT NOT
    NULL REFERENCES runs(id), data TEXT NOT NULL);




CREATE TABLE projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, description TEXT
    NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT
    NOT NULL, updated_by TEXT NOT NULL
, worker_documents TEXT NOT NULL DEFAULT '[]');

CREATE TABLE question_revisions (
 workspace_id TEXT NOT NULL, question_id TEXT NOT NULL, revision INTEGER NOT NULL, data
    TEXT NOT NULL, PRIMARY KEY(workspace_id, question_id, revision), FOREIGN KEY (workspace_id,
    question_id) REFERENCES questions(workspace_id, id)
);

CREATE TABLE questions (
 number INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    id TEXT NOT NULL, task_id TEXT, status TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(workspace_id,
    id), FOREIGN KEY (workspace_id, task_id) REFERENCES tasks(workspace_id, id)
);

CREATE TABLE result_versions (
 number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, workspace_id TEXT
    NOT NULL REFERENCES workspaces(id), task_id TEXT NOT NULL, run_id TEXT NOT NULL
    REFERENCES runs(id), version INTEGER NOT NULL, status TEXT NOT NULL, data TEXT
    NOT NULL, UNIQUE(workspace_id, task_id, version), FOREIGN KEY(workspace_id, task_id)
    REFERENCES tasks(workspace_id, id)
);

CREATE TABLE review_attempts (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT
    NULL UNIQUE, review_id TEXT NOT NULL REFERENCES task_reviews(id), data TEXT NOT
    NULL);

CREATE TABLE reviewer_settings (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 entity_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(workspace_id,entity_id));

CREATE TABLE run_activity (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), revision INTEGER NOT NULL, data TEXT
    NOT NULL
);

CREATE TABLE runs (
 number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), task_id TEXT NOT NULL,
 subtask_id TEXT, status TEXT NOT NULL, data TEXT NOT NULL, assignment TEXT NOT NULL,
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id),
 FOREIGN KEY(workspace_id,task_id,subtask_id) REFERENCES subtasks(workspace_id,task_id,id)
);

CREATE VIRTUAL TABLE search_docs USING fts5(project_id UNINDEXED, entity UNINDEXED,
    identity UNINDEXED, revision UNINDEXED, kind UNINDEXED, task_id UNINDEXED,
    created_at UNINDEXED, title, body, workspace_id UNINDEXED, tokenize='unicode61');

CREATE TABLE storage_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), workspace_id
    TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision >= 0));

CREATE TABLE task_replies (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, id TEXT PRIMARY KEY, created_at
    TEXT NOT NULL, data TEXT NOT NULL, FOREIGN KEY(workspace_id, task_id) REFERENCES
    tasks(workspace_id, id)
);

CREATE TABLE task_reviews (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL
    UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), task_id TEXT NOT
    NULL, result_id TEXT REFERENCES result_versions(id), binding TEXT NOT
    NULL, status TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(result_id, binding), FOREIGN
    KEY(workspace_id, task_id) REFERENCES tasks(workspace_id, id));

CREATE TABLE task_revisions (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL, data
    TEXT NOT NULL, PRIMARY KEY (workspace_id, task_id, revision), FOREIGN KEY (workspace_id,
    task_id) REFERENCES tasks(workspace_id, id)
);

CREATE TABLE tasks (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), id TEXT NOT NULL,
 position INTEGER NOT NULL, data TEXT NOT NULL, number INTEGER NOT NULL, key TEXT NOT NULL,
 project_id TEXT REFERENCES projects(id), scheduling_problem TEXT, PRIMARY KEY(workspace_id,id),
 UNIQUE(workspace_id,number), UNIQUE(workspace_id,key),
 FOREIGN KEY(workspace_id,project_id) REFERENCES workspace_projects(workspace_id,project_id)
);
CREATE TABLE task_dependencies (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, prerequisite_id TEXT NOT NULL,
 PRIMARY KEY(workspace_id,task_id,prerequisite_id), CHECK(task_id != prerequisite_id),
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id),
 FOREIGN KEY(workspace_id,prerequisite_id) REFERENCES tasks(workspace_id,id)
);
CREATE TABLE subtasks (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, id TEXT NOT NULL,
 number INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(workspace_id,task_id,id),
 UNIQUE(workspace_id,task_id,number),
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id)
);
CREATE TABLE task_assemblies (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
 signature TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(workspace_id,task_id,signature),
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id)
);
CREATE TABLE subtask_revisions (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, subtask_id TEXT NOT NULL,
 revision INTEGER NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(workspace_id,task_id,subtask_id,revision),
 FOREIGN KEY(workspace_id,task_id,subtask_id) REFERENCES subtasks(workspace_id,task_id,id)
);
CREATE TABLE subtask_dependencies (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, subtask_id TEXT NOT NULL,
 prerequisite_id TEXT NOT NULL, PRIMARY KEY(workspace_id,task_id,subtask_id,prerequisite_id),
 CHECK(subtask_id != prerequisite_id),
 FOREIGN KEY(workspace_id,task_id,subtask_id) REFERENCES subtasks(workspace_id,task_id,id),
 FOREIGN KEY(workspace_id,task_id,prerequisite_id) REFERENCES subtasks(workspace_id,task_id,id)
);

CREATE TABLE update_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);


CREATE TABLE worker_check_grants (project_id TEXT PRIMARY KEY REFERENCES projects(id),
    data TEXT NOT NULL);



CREATE TABLE workspace_coordinator_settings (workspace_id TEXT PRIMARY KEY REFERENCES
    workspaces(id), revision INTEGER NOT NULL, selection TEXT);

CREATE TABLE workspace_document_revisions (workspace_id TEXT NOT NULL, id TEXT NOT
    NULL, revision INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at
    TEXT NOT NULL, author TEXT NOT NULL, restored_from INTEGER, PRIMARY KEY(workspace_id,
    id, revision), FOREIGN KEY(workspace_id, id) REFERENCES workspace_documents(workspace_id,
    id));

CREATE TABLE workspace_documents (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    id TEXT NOT NULL, revision INTEGER NOT NULL, deleted_at TEXT, deleted_by TEXT,
    PRIMARY KEY(workspace_id, id));

CREATE TABLE document_usage (workspace_id TEXT NOT NULL, id TEXT NOT NULL,
    revision INTEGER NOT NULL, projectless TEXT CHECK(projectless IN ('required','reference')),
    PRIMARY KEY(workspace_id,id),
    FOREIGN KEY(workspace_id,id) REFERENCES workspace_documents(workspace_id,id));


CREATE TABLE workspace_labels (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    id TEXT NOT NULL, name TEXT NOT NULL, color TEXT NOT NULL, guidance TEXT NOT NULL,
    revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, deleted_at TEXT, PRIMARY KEY(workspace_id, id));

CREATE TABLE workspace_projects (project_id TEXT PRIMARY KEY REFERENCES projects(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id), UNIQUE(workspace_id,project_id));

CREATE TABLE workspace_worker_settings (workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
    data TEXT NOT NULL);

CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT
    NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), created_at TEXT NOT
    NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL);

CREATE INDEX activity_scope ON activity(workspace_id, task_id, sequence);

CREATE INDEX announcement_pending ON announcement_events(state, created_at);

CREATE INDEX attachment_project ON attachments(workspace_id, task_id);

CREATE INDEX attachment_workspace ON attachments(workspace_id);

CREATE INDEX attention_sources_project ON attention_sources(workspace_id);

CREATE UNIQUE INDEX coordinator_active ON coordinator_turns(workspace_id) WHERE status
    IN ('starting', 'running', 'stopping', 'uncertain');

CREATE INDEX coordinator_generation_workspace ON coordinator_session_generations(workspace_id);

CREATE INDEX coordinator_history ON coordinator_turns(conversation_id, number);

CREATE INDEX coordinator_workspace_messages ON coordinator_turns(workspace_id, number);

CREATE INDEX coordinator_workspaces ON coordinator_conversations(workspace_id, number);


CREATE INDEX inspections_source ON inspections(project_id, result_id, number);

CREATE INDEX integrations_project ON integrations(project_id, number);






CREATE INDEX output_run ON output_files(run_id, number);

CREATE INDEX output_workspace ON output_files(workspace_id, number);

CREATE INDEX permissions_binding ON agent_permissions(binding, status);

CREATE INDEX permissions_project ON agent_permissions(workspace_id, task_id, number);

CREATE INDEX permissions_workspace ON agent_permissions(workspace_id, number);

CREATE INDEX question_scope ON questions(workspace_id, task_id, status);

CREATE INDEX reply_scope ON task_replies(workspace_id, task_id, created_at);

CREATE INDEX result_work ON result_versions(workspace_id, status, number);

CREATE INDEX review_attempt_owner ON review_attempts(review_id, number);

CREATE UNIQUE INDEX run_owner ON runs(workspace_id, task_id, coalesce(subtask_id,''))
WHERE status IN ('preparing', 'running', 'stopping', 'uncertain');

CREATE INDEX runs_project ON runs(workspace_id, task_id, number);

CREATE INDEX task_reviews_task ON task_reviews(workspace_id, task_id, number);

CREATE UNIQUE INDEX task_scope_key ON tasks(workspace_id, key);

CREATE UNIQUE INDEX workspace_label_names ON workspace_labels(workspace_id, name COLLATE
    NOCASE) WHERE deleted_at IS NULL;

CREATE INDEX workspace_members ON workspace_projects(workspace_id, project_id);










CREATE TRIGGER search_activity_insert AFTER INSERT ON activity BEGIN INSERT INTO search_docs
    VALUES (NULL, 'activity', new.id, new.sequence, new.kind, new.task_id,
    new.created_at, new.kind, new.body, new.workspace_id); END;



CREATE TRIGGER search_project_membership AFTER INSERT ON workspace_projects BEGIN
    UPDATE search_docs SET workspace_id=new.workspace_id WHERE project_id=new.project_id;
    END;

CREATE TRIGGER search_projects_insert AFTER INSERT ON projects BEGIN INSERT INTO search_docs
    VALUES (new.id, 'project', new.id, new.revision, 'project', NULL, new.updated_at,
    new.name, new.description, (SELECT workspace_id FROM workspace_projects WHERE
    project_id=new.id)); END;

CREATE TRIGGER search_projects_update AFTER UPDATE ON projects BEGIN DELETE FROM search_docs
    WHERE project_id=new.id AND entity='project';INSERT INTO search_docs VALUES (new.id,
    'project', new.id, new.revision, 'project', NULL, new.updated_at, new.name, new.description,
    (SELECT workspace_id FROM workspace_projects WHERE project_id=new.id)); END;

CREATE TRIGGER search_question_revisions_insert AFTER INSERT ON question_revisions
    BEGIN INSERT INTO search_docs VALUES (NULL, 'question', new.question_id,
    new.revision, 'question', json_extract(new.data, '$.task_id'), json_extract(new.data,
    '$.updated_at'), json_extract(new.data, '$.question'), coalesce(json_extract(new.data,
    '$.context'), '')||char(10)||coalesce(json_extract(new.data, '$.recommendation'),
    '')||char(10)||coalesce(json_extract(new.data, '$.answer'), ''), new.workspace_id); END;

CREATE TRIGGER search_result_versions_insert AFTER INSERT ON result_versions BEGIN
    INSERT INTO search_docs VALUES (NULL, 'result', new.id, json_extract(new.data,
    '$.revision'), 'result', new.task_id, json_extract(new.data, '$.created_at'),
    json_extract(new.data, '$.report.summary'), coalesce(json_extract(new.data, '$.report.checks'),
    '')||char(10)||coalesce(json_extract(new.data, '$.report.limitations'), '')||char(10)||
    coalesce(json_extract(new.data,
    '$.feedback'), '')||char(10)||coalesce(json_extract(new.data, '$.problem'), ''),
    new.workspace_id);
    END;

CREATE TRIGGER search_result_versions_update AFTER UPDATE ON result_versions BEGIN
    DELETE FROM search_docs WHERE workspace_id=new.workspace_id AND entity='result' AND
    identity=new.id;INSERT INTO search_docs VALUES (NULL, 'result', new.id,
    json_extract(new.data, '$.revision'), 'result', new.task_id, json_extract(new.data,
    '$.created_at'), json_extract(new.data, '$.report.summary'), coalesce(json_extract(new.data,
    '$.report.checks'), '')||char(10)||coalesce(json_extract(new.data, '$.report.limitations'),
    '')||char(10)||coalesce(json_extract(new.data, '$.feedback'), '')||char(10)||
    coalesce(json_extract(new.data, '$.problem'), ''),
    new.workspace_id);
    END;

CREATE TRIGGER search_task_revisions_insert AFTER INSERT ON task_revisions BEGIN INSERT
    INTO search_docs VALUES (NULL, 'task', new.task_id, new.revision, 'task',
    new.task_id, json_extract(new.data, '$.updated_at'), json_extract(new.data, '$.title'),
    json_extract(new.data, '$.body'), new.workspace_id); END;


INSERT INTO storage_metadata VALUES (1, lower(hex(randomblob(16))), 0);

INSERT INTO harness_settings VALUES ('codex', 1, NULL, NULL), ('claude-code', 1, NULL,
    NULL), ('pi', 1, NULL, NULL);

PRAGMA user_version = 90;
"""

SCHEMA += TASK_DELETIONS + ";"

SCHEMA += TABLES + TRIGGERS
