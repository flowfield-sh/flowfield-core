BEGIN TRANSACTION;
CREATE TABLE activity (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, workspace_id TEXT
    NOT NULL REFERENCES workspaces(id), task_id TEXT, kind TEXT NOT NULL CHECK(kind
    IN ('note', 'handoff', 'event')), body TEXT NOT NULL, author TEXT NOT NULL, created_at
    TEXT NOT NULL, supersedes TEXT UNIQUE REFERENCES activity(id), task_revision INTEGER,
    question_id TEXT, FOREIGN KEY (workspace_id, task_id) REFERENCES tasks(workspace_id,
    id)
);
INSERT INTO "activity" VALUES(1,'8bb0da6111414df68a495edd886e22fb','18c14488213041e1914bdc3705617533','unscheduled','event','Task created in Backlog.','human','2026-10-30T18:06:17.055767+00:00',NULL,1,NULL);
INSERT INTO "activity" VALUES(2,'f4a73ed62ee346f8bf7dc032025f3c4d','18c14488213041e1914bdc3705617533','unprepared','event','Task created in Up next.','human','2026-10-30T18:06:17.059163+00:00',NULL,1,NULL);
INSERT INTO "activity" VALUES(3,'eebc851afcc648e4ba0985697e606908','18c14488213041e1914bdc3705617533','ready','event','Task created in Up next.','human','2026-10-30T18:06:17.062322+00:00',NULL,1,NULL);
INSERT INTO "activity" VALUES(4,'5c686868a0734c98a9f38343b588fb4d','18c14488213041e1914bdc3705617533','ready','event','Assignment checked and published.','human','2026-10-30T18:06:17.083873+00:00',NULL,2,NULL);
INSERT INTO "activity" VALUES(5,'dcd65f67d0404da1aaf77ef17f3f25bf','18c14488213041e1914bdc3705617533','changed','event','Task created in Up next.','human','2026-10-30T18:06:17.087158+00:00',NULL,1,NULL);
INSERT INTO "activity" VALUES(6,'b4f89318f59644baa518cbf35584cea7','18c14488213041e1914bdc3705617533','changed','event','Assignment checked and published.','human','2026-10-30T18:06:17.088471+00:00',NULL,2,NULL);
INSERT INTO "activity" VALUES(7,'80d0cd11b2d440029250e11c9f9c9547','18c14488213041e1914bdc3705617533','changed','event','Description updated.

Assignment changed; coordinator must republish before work starts.','human','2026-10-30T18:06:17.091539+00:00',NULL,3,NULL);
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
CREATE TABLE document_usage (workspace_id TEXT NOT NULL, id TEXT NOT NULL,
    revision INTEGER NOT NULL, projectless TEXT CHECK(projectless IN ('required','reference')),
    PRIMARY KEY(workspace_id,id),
    FOREIGN KEY(workspace_id,id) REFERENCES workspace_documents(workspace_id,id));
CREATE TABLE execution_local (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), data TEXT NOT NULL
);
CREATE TABLE harness_catalogs (harness TEXT PRIMARY KEY CHECK(harness IN ('codex',
    'claude-code', 'pi')), data TEXT NOT NULL);
CREATE TABLE harness_settings (harness TEXT PRIMARY KEY CHECK(harness IN ('codex',
    'claude-code', 'pi')), revision INTEGER NOT NULL CHECK(revision>=1), executable
    TEXT, config_directory TEXT);
INSERT INTO "harness_settings" VALUES('codex',1,NULL,NULL);
INSERT INTO "harness_settings" VALUES('claude-code',1,NULL,NULL);
INSERT INTO "harness_settings" VALUES('pi',1,NULL,NULL);
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
    UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), run_id TEXT
    REFERENCES runs(id), data TEXT NOT NULL,
    review_attempt_id TEXT REFERENCES review_attempts(id),
    CHECK ((run_id IS NOT NULL) != (review_attempt_id IS NOT NULL)));
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
INSERT INTO "reviewer_settings" VALUES('18c14488213041e1914bdc3705617533','','{"revision":1,"enabled":true,"selection":{"harness":"codex","model":"scripted-fixture","effort":null,"mode":null,"fast":null}}');
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
INSERT INTO "search_docs" VALUES(NULL,'activity','8bb0da6111414df68a495edd886e22fb',1,'event','unscheduled','2026-10-30T18:06:17.055767+00:00','event','Task created in Backlog.','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','unscheduled',1,'task','unscheduled','2026-10-30T18:06:17.055767+00:00','Unscheduled idea','','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'activity','f4a73ed62ee346f8bf7dc032025f3c4d',2,'event','unprepared','2026-10-30T18:06:17.059163+00:00','event','Task created in Up next.','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','unprepared',1,'task','unprepared','2026-10-30T18:06:17.059163+00:00','Captured in Up next','Write findings','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'activity','eebc851afcc648e4ba0985697e606908',3,'event','ready','2026-10-30T18:06:17.062322+00:00','event','Task created in Up next.','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','ready',1,'task','ready','2026-10-30T18:06:17.062322+00:00','Ready in Up next','Write findings','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'activity','5c686868a0734c98a9f38343b588fb4d',4,'event','ready','2026-10-30T18:06:17.083873+00:00','event','Assignment checked and published.','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','ready',2,'task','ready','2026-10-30T18:06:17.083873+00:00','Ready in Up next','Write findings','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'activity','dcd65f67d0404da1aaf77ef17f3f25bf',5,'event','changed','2026-10-30T18:06:17.087158+00:00','event','Task created in Up next.','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','changed',1,'task','changed','2026-10-30T18:06:17.087158+00:00','Changed in Up next','Original scope','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'activity','b4f89318f59644baa518cbf35584cea7',6,'event','changed','2026-10-30T18:06:17.088471+00:00','event','Assignment checked and published.','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','changed',2,'task','changed','2026-10-30T18:06:17.088471+00:00','Changed in Up next','Original scope','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'activity','80d0cd11b2d440029250e11c9f9c9547',7,'event','changed','2026-10-30T18:06:17.091539+00:00','event','Description updated.

Assignment changed; coordinator must republish before work starts.','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','changed',3,'task','changed','2026-10-30T18:06:17.091539+00:00','Changed in Up next','Revised scope','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','unprepared',2,'task','unprepared','2026-10-31T12:03:05.418083+00:00','Captured in Up next','Write findings','18c14488213041e1914bdc3705617533');
INSERT INTO "search_docs" VALUES(NULL,'task','changed',4,'task','changed','2026-10-31T12:03:05.418083+00:00','Changed in Up next','Revised scope','18c14488213041e1914bdc3705617533');
CREATE TABLE storage_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), workspace_id
    TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision >= 0));
INSERT INTO "storage_metadata" VALUES(1,'f6120e9311d9df9481b384bdd9898be5',6);
CREATE TABLE subtask_dependencies (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, subtask_id TEXT NOT NULL,
 prerequisite_id TEXT NOT NULL, PRIMARY KEY(workspace_id,task_id,subtask_id,prerequisite_id),
 CHECK(subtask_id != prerequisite_id),
 FOREIGN KEY(workspace_id,task_id,subtask_id) REFERENCES subtasks(workspace_id,task_id,id),
 FOREIGN KEY(workspace_id,task_id,prerequisite_id) REFERENCES subtasks(workspace_id,task_id,id)
);
CREATE TABLE subtask_revisions (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, subtask_id TEXT NOT NULL,
 revision INTEGER NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(workspace_id,task_id,subtask_id,revision),
 FOREIGN KEY(workspace_id,task_id,subtask_id) REFERENCES subtasks(workspace_id,task_id,id)
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
CREATE TABLE task_deletions (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), task_id TEXT NOT NULL,
 key TEXT NOT NULL, number INTEGER NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(workspace_id,task_id), UNIQUE(workspace_id,key), UNIQUE(workspace_id,number)
);
CREATE TABLE task_dependencies (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, prerequisite_id TEXT NOT NULL,
 PRIMARY KEY(workspace_id,task_id,prerequisite_id), CHECK(task_id != prerequisite_id),
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id),
 FOREIGN KEY(workspace_id,prerequisite_id) REFERENCES tasks(workspace_id,id)
);
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
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','unscheduled',1,'{"id":"unscheduled","title":"Unscheduled idea","body":"","revision":1,"created_at":"2026-10-30T18:06:17.055760+00:00","updated_at":"2026-10-30T18:06:17.055767+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-1","labels":[],"status":"backlog","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','unprepared',1,'{"id":"unprepared","title":"Captured in Up next","body":"Write findings","revision":1,"created_at":"2026-10-30T18:06:17.059160+00:00","updated_at":"2026-10-30T18:06:17.059163+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-2","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','ready',1,'{"id":"ready","title":"Ready in Up next","body":"Write findings","revision":1,"created_at":"2026-10-30T18:06:17.062319+00:00","updated_at":"2026-10-30T18:06:17.062322+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-3","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','ready',2,'{"id":"ready","title":"Ready in Up next","body":"Write findings","revision":2,"created_at":"2026-10-30T18:06:17.062319+00:00","updated_at":"2026-10-30T18:06:17.083873+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-30T18:06:17.083873+00:00","completion":"report","target_branch":null,"documents":[{"workspace_id":"18c14488213041e1914bdc3705617533","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-3","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','changed',1,'{"id":"changed","title":"Changed in Up next","body":"Original scope","revision":1,"created_at":"2026-10-30T18:06:17.087155+00:00","updated_at":"2026-10-30T18:06:17.087158+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-4","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','changed',2,'{"id":"changed","title":"Changed in Up next","body":"Original scope","revision":2,"created_at":"2026-10-30T18:06:17.087155+00:00","updated_at":"2026-10-30T18:06:17.088471+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-30T18:06:17.088471+00:00","completion":"report","target_branch":null,"documents":[{"workspace_id":"18c14488213041e1914bdc3705617533","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-4","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','changed',3,'{"id":"changed","title":"Changed in Up next","body":"Revised scope","revision":3,"created_at":"2026-10-30T18:06:17.087155+00:00","updated_at":"2026-10-30T18:06:17.091539+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":2,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-30T18:06:17.088471+00:00","completion":"report","target_branch":null,"documents":[{"workspace_id":"18c14488213041e1914bdc3705617533","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-4","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','unprepared',2,'{"id": "unprepared", "title": "Captured in Up next", "body": "Write findings", "revision": 2, "created_at": "2026-10-30T18:06:17.059160+00:00", "updated_at": "2026-10-31T12:03:05.418083+00:00", "updated_by": "flowfield", "workspace_id": "18c14488213041e1914bdc3705617533", "project_id": null, "acceptance_criteria": "", "input_files": [], "agreement_revision": 1, "publication": null, "key": "T-2", "labels": [], "status": "backlog", "archived": false, "dependencies": [], "documents": [], "reconciliation_reason": null, "change_note": "Moved to Backlog during upgrade to preserve unscheduled work."}');
INSERT INTO "task_revisions" VALUES('18c14488213041e1914bdc3705617533','changed',4,'{"id": "changed", "title": "Changed in Up next", "body": "Revised scope", "revision": 4, "created_at": "2026-10-30T18:06:17.087155+00:00", "updated_at": "2026-10-31T12:03:05.418083+00:00", "updated_by": "flowfield", "workspace_id": "18c14488213041e1914bdc3705617533", "project_id": null, "acceptance_criteria": "", "input_files": [], "agreement_revision": 2, "publication": {"agreement_revision": 1, "task_revision": 1, "author": "human", "created_at": "2026-10-30T18:06:17.088471+00:00", "completion": "report", "target_branch": null, "documents": [{"workspace_id": "18c14488213041e1914bdc3705617533", "id": "guidance", "revision": 1, "title": "Workspace guidance", "characters": 0, "bytes": 0, "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "purpose": "required"}]}, "key": "T-4", "labels": [], "status": "backlog", "archived": false, "dependencies": [], "documents": [], "reconciliation_reason": null, "change_note": "Moved to Backlog during upgrade to preserve unscheduled work."}');
CREATE TABLE tasks (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), id TEXT NOT NULL,
 position INTEGER NOT NULL, data TEXT NOT NULL, number INTEGER NOT NULL, key TEXT NOT NULL,
 project_id TEXT REFERENCES projects(id), scheduling_problem TEXT, PRIMARY KEY(workspace_id,id),
 UNIQUE(workspace_id,number), UNIQUE(workspace_id,key),
 FOREIGN KEY(workspace_id,project_id) REFERENCES workspace_projects(workspace_id,project_id)
);
INSERT INTO "tasks" VALUES('18c14488213041e1914bdc3705617533','unscheduled',0,'{"id":"unscheduled","title":"Unscheduled idea","body":"","revision":1,"created_at":"2026-10-30T18:06:17.055760+00:00","updated_at":"2026-10-30T18:06:17.055767+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-1","labels":[],"status":"backlog","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}',1,'T-1',NULL,NULL);
INSERT INTO "tasks" VALUES('18c14488213041e1914bdc3705617533','unprepared',1,'{"id": "unprepared", "title": "Captured in Up next", "body": "Write findings", "revision": 2, "created_at": "2026-10-30T18:06:17.059160+00:00", "updated_at": "2026-10-31T12:03:05.418083+00:00", "updated_by": "flowfield", "workspace_id": "18c14488213041e1914bdc3705617533", "project_id": null, "acceptance_criteria": "", "input_files": [], "agreement_revision": 1, "publication": null, "key": "T-2", "labels": [], "status": "backlog", "archived": false, "dependencies": [], "documents": [], "reconciliation_reason": null, "change_note": "Moved to Backlog during upgrade to preserve unscheduled work."}',2,'T-2',NULL,NULL);
INSERT INTO "tasks" VALUES('18c14488213041e1914bdc3705617533','ready',2,'{"id":"ready","title":"Ready in Up next","body":"Write findings","revision":2,"created_at":"2026-10-30T18:06:17.062319+00:00","updated_at":"2026-10-30T18:06:17.083873+00:00","updated_by":"human","workspace_id":"18c14488213041e1914bdc3705617533","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-30T18:06:17.083873+00:00","completion":"report","target_branch":null,"documents":[{"workspace_id":"18c14488213041e1914bdc3705617533","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-3","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}',3,'T-3',NULL,NULL);
INSERT INTO "tasks" VALUES('18c14488213041e1914bdc3705617533','changed',3,'{"id": "changed", "title": "Changed in Up next", "body": "Revised scope", "revision": 4, "created_at": "2026-10-30T18:06:17.087155+00:00", "updated_at": "2026-10-31T12:03:05.418083+00:00", "updated_by": "flowfield", "workspace_id": "18c14488213041e1914bdc3705617533", "project_id": null, "acceptance_criteria": "", "input_files": [], "agreement_revision": 2, "publication": {"agreement_revision": 1, "task_revision": 1, "author": "human", "created_at": "2026-10-30T18:06:17.088471+00:00", "completion": "report", "target_branch": null, "documents": [{"workspace_id": "18c14488213041e1914bdc3705617533", "id": "guidance", "revision": 1, "title": "Workspace guidance", "characters": 0, "bytes": 0, "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "purpose": "required"}]}, "key": "T-4", "labels": [], "status": "backlog", "archived": false, "dependencies": [], "documents": [], "reconciliation_reason": null, "change_note": "Moved to Backlog during upgrade to preserve unscheduled work."}',4,'T-4',NULL,NULL);
CREATE TABLE update_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE usage_checkpoints (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), series TEXT NOT NULL,
 execution INTEGER NOT NULL REFERENCES usage_executions(number) ON DELETE CASCADE,
 data TEXT NOT NULL, PRIMARY KEY(workspace_id,series)
);
CREATE TABLE usage_closed_scopes (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), series TEXT NOT NULL,
 PRIMARY KEY(workspace_id,series)
);
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
CREATE TABLE usage_metadata (
 id INTEGER PRIMARY KEY CHECK(id=1), collection_started_at TEXT NOT NULL);
INSERT INTO "usage_metadata" VALUES(1,'2026-10-30T18:06:17.018+00:00');
CREATE TABLE usage_observations (
 number INTEGER PRIMARY KEY AUTOINCREMENT,
 execution INTEGER NOT NULL REFERENCES usage_executions(number) ON DELETE CASCADE,
 series TEXT NOT NULL, event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 received_at TEXT NOT NULL, data TEXT NOT NULL, baseline TEXT,
 delta TEXT NOT NULL, disposition TEXT NOT NULL, cost TEXT,
 UNIQUE(execution,series,event_id,fingerprint)
);
CREATE TABLE worker_check_grants (project_id TEXT PRIMARY KEY REFERENCES projects(id),
    data TEXT NOT NULL);
CREATE TABLE workspace_coordinator_settings (workspace_id TEXT PRIMARY KEY REFERENCES
    workspaces(id), revision INTEGER NOT NULL, selection TEXT);
INSERT INTO "workspace_coordinator_settings" VALUES('18c14488213041e1914bdc3705617533',1,'{"harness":"codex","model":"scripted-fixture","effort":null,"mode":null,"fast":null}');
CREATE TABLE workspace_document_revisions (workspace_id TEXT NOT NULL, id TEXT NOT
    NULL, revision INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at
    TEXT NOT NULL, author TEXT NOT NULL, restored_from INTEGER, PRIMARY KEY(workspace_id,
    id, revision), FOREIGN KEY(workspace_id, id) REFERENCES workspace_documents(workspace_id,
    id));
INSERT INTO "workspace_document_revisions" VALUES('18c14488213041e1914bdc3705617533','guidance',1,'Workspace guidance','','2026-10-30T18:06:17.020714+00:00','human',NULL);
CREATE TABLE workspace_documents (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    id TEXT NOT NULL, revision INTEGER NOT NULL, deleted_at TEXT, deleted_by TEXT,
    PRIMARY KEY(workspace_id, id));
INSERT INTO "workspace_documents" VALUES('18c14488213041e1914bdc3705617533','guidance',1,NULL,NULL);
CREATE TABLE workspace_labels (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    id TEXT NOT NULL, name TEXT NOT NULL, color TEXT NOT NULL, guidance TEXT NOT NULL,
    revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, deleted_at TEXT, PRIMARY KEY(workspace_id, id));
INSERT INTO "workspace_labels" VALUES('18c14488213041e1914bdc3705617533','feature','Feature','blue','New or expanded product behavior.',1,'2026-10-30T18:06:17.020714+00:00','2026-10-30T18:06:17.020714+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('18c14488213041e1914bdc3705617533','bug','Bug','orange','Correct behavior that does not match the intended outcome.',1,'2026-10-30T18:06:17.020714+00:00','2026-10-30T18:06:17.020714+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('18c14488213041e1914bdc3705617533','maintenance','Maintenance','slate','Upkeep of existing code, dependencies or documentation.',1,'2026-10-30T18:06:17.020714+00:00','2026-10-30T18:06:17.020714+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('18c14488213041e1914bdc3705617533','investigation','Investigation','violet','Answer a question or reduce uncertainty.',1,'2026-10-30T18:06:17.020714+00:00','2026-10-30T18:06:17.020714+00:00','human',NULL);
CREATE TABLE workspace_projects (project_id TEXT PRIMARY KEY REFERENCES projects(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id), UNIQUE(workspace_id,project_id));
CREATE TABLE workspace_worker_settings (workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
    data TEXT NOT NULL);
INSERT INTO "workspace_worker_settings" VALUES('18c14488213041e1914bdc3705617533','{"workspace_id":"18c14488213041e1914bdc3705617533","revision":1,"selection":{"harness":"codex","model":"scripted-fixture","effort":null,"mode":null,"fast":false},"max_parallel":5,"auto_start_queue":false,"enabled":false,"problem":null}');
CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT
    NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), created_at TEXT NOT
    NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL);
INSERT INTO "workspaces" VALUES('18c14488213041e1914bdc3705617533','Published lifecycle fixture','',1,'2026-10-30T18:06:17.020714+00:00','2026-10-30T18:06:17.020714+00:00','human');
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
CREATE INDEX usage_workspace ON usage_executions(workspace_id,number);
CREATE INDEX usage_task ON usage_executions(workspace_id,task_id,number);
CREATE INDEX usage_receipt ON usage_observations(received_at,execution);
CREATE INDEX usage_evidence ON usage_observations(execution,number);
CREATE TRIGGER usage_execution_scope_delete BEFORE DELETE ON usage_executions BEGIN
 INSERT OR IGNORE INTO usage_closed_scopes(workspace_id,series)
 SELECT old.workspace_id,series FROM usage_observations WHERE execution=old.number;
END;
CREATE TRIGGER usage_run_insert AFTER INSERT ON runs BEGIN
    INSERT OR IGNORE INTO usage_executions
    (workspace_id,source,source_id,task_id,related_task_id,project_id,parent_id,subtask_id,
    role,purpose,outcome,harness,requested_model,created_at,legacy,timing,reasons)
    SELECT r.workspace_id,'run',r.id,r.task_id,NULL,json_extract(r.data,'$.project_id'),NULL,r.subtask_id,
    'worker',coalesce(json_extract(r.data,'$.launch_reason'),'unknown'),r.status,json_extract(r.data,'$.agent_settings.choice.harness'),json_extract(r.data,'$.model'),json_extract(r.data,'$.created_at'),
    0,'not-dispatched',
    '[]'
    FROM runs r WHERE (coalesce(json_extract(r.data,'$.purpose'),'work') != 'assembly') AND r.id=new.id;
    END;
CREATE TRIGGER usage_run_delete AFTER DELETE ON runs BEGIN
    DELETE FROM usage_executions WHERE source='run' AND source_id=old.id;
    END;
CREATE TRIGGER usage_run_update AFTER UPDATE ON runs BEGIN
    UPDATE usage_executions SET outcome=new.status,
    harness=coalesce(NULL,harness), requested_model=coalesce(NULL,requested_model)
    WHERE source='run' AND source_id=new.id;
    END;
CREATE TRIGGER usage_review_attempt_insert AFTER INSERT ON review_attempts BEGIN
    INSERT OR IGNORE INTO usage_executions
    (workspace_id,source,source_id,task_id,related_task_id,project_id,parent_id,subtask_id,
    role,purpose,outcome,harness,requested_model,created_at,legacy,timing,reasons)
    SELECT j.workspace_id,'review_attempt',r.id,j.task_id,NULL,(SELECT project_id FROM tasks WHERE workspace_id=j.workspace_id AND id=j.task_id),r.review_id,json_extract(j.data,'$.responding_subtask_id'),
    CASE json_extract(r.data,'$.role') WHEN 'implementer' THEN 'worker' ELSE 'reviewer' END,coalesce(json_extract(r.data,'$.launch_reason'),'unknown'),coalesce(json_extract(r.data,'$.status'),'unknown'),json_extract(r.data,'$.requested_agent.harness'),json_extract(r.data,'$.requested_agent.model'),json_extract(r.data,'$.created_at'),
    0,'not-dispatched',
    '[]'
    FROM review_attempts r JOIN task_reviews j ON j.id=r.review_id WHERE (1) AND r.id=new.id;
    END;
CREATE TRIGGER usage_review_attempt_delete AFTER DELETE ON review_attempts BEGIN
    DELETE FROM usage_executions WHERE source='review_attempt' AND source_id=old.id;
    END;
CREATE TRIGGER usage_review_attempt_update AFTER UPDATE ON review_attempts BEGIN
    UPDATE usage_executions SET outcome=coalesce(json_extract(new.data,'$.status'),'unknown'),
    harness=coalesce(json_extract(new.data,'$.requested_agent.harness'),harness), requested_model=coalesce(json_extract(new.data,'$.requested_agent.model'),requested_model)
    WHERE source='review_attempt' AND source_id=new.id;
    END;
CREATE TRIGGER usage_coordinator_turn_insert AFTER INSERT ON coordinator_turns BEGIN
    INSERT OR IGNORE INTO usage_executions
    (workspace_id,source,source_id,task_id,related_task_id,project_id,parent_id,subtask_id,
    role,purpose,outcome,harness,requested_model,created_at,legacy,timing,reasons)
    SELECT r.workspace_id,'coordinator_turn',r.id,NULL,json_extract(r.data,'$.task_context.task_id'),NULL,r.conversation_id,NULL,
    'coordinator','message',r.status,json_extract(r.data,'$.settings.choice.harness'),json_extract(r.data,'$.settings.choice.model'),json_extract(r.data,'$.created_at'),
    0,'not-dispatched',
    '[]'
    FROM coordinator_turns r WHERE (1) AND r.id=new.id;
    END;
CREATE TRIGGER usage_coordinator_turn_delete AFTER DELETE ON coordinator_turns BEGIN
    DELETE FROM usage_executions WHERE source='coordinator_turn' AND source_id=old.id;
    END;
CREATE TRIGGER usage_coordinator_turn_update AFTER UPDATE ON coordinator_turns BEGIN
    UPDATE usage_executions SET outcome=new.status,
    harness=coalesce(NULL,harness), requested_model=coalesce(NULL,requested_model)
    WHERE source='coordinator_turn' AND source_id=new.id;
    END;
CREATE TRIGGER usage_related_delete AFTER DELETE ON tasks BEGIN
UPDATE usage_executions SET related_task_id=NULL
WHERE workspace_id=old.workspace_id AND related_task_id=old.id;
END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('activity',7);
COMMIT;
PRAGMA user_version=93;
