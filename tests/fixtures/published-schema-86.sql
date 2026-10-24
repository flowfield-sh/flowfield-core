BEGIN TRANSACTION;
CREATE TABLE activity (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, workspace_id TEXT
    NOT NULL REFERENCES workspaces(id), task_id TEXT, kind TEXT NOT NULL CHECK(kind
    IN ('note', 'handoff', 'event')), body TEXT NOT NULL, author TEXT NOT NULL, created_at
    TEXT NOT NULL, supersedes TEXT UNIQUE REFERENCES activity(id), task_revision INTEGER,
    question_id TEXT, FOREIGN KEY (workspace_id, task_id) REFERENCES tasks(workspace_id,
    id)
);
INSERT INTO "activity" VALUES(1,'76e27d6564474539801d09f352b054be','harbor','work','event','Task created in Up next.','human','2026-10-24T14:58:55.207804+00:00',NULL,1,NULL);
INSERT INTO "activity" VALUES(2,'0df13b1ff5864a1798eb81ce3c1ed9bd','harbor','work','event','Assignment checked and published.','human','2026-10-24T14:58:55.212070+00:00',NULL,2,NULL);
INSERT INTO "activity" VALUES(3,'ed3fd46c4ce74b17abe37f7acf227f34','harbor','work','event','Up next → In progress.','flowfield','2026-10-24T14:58:55.237227+00:00',NULL,3,NULL);
INSERT INTO "activity" VALUES(4,'33dffc76ee4f43c98ef3a7ed2c4f31a8','harbor','work','event','In progress → In review.','flowfield','2026-10-24T14:58:56.121442+00:00',NULL,5,NULL);
INSERT INTO "activity" VALUES(5,'41e1f7fec93c456e8a0dccfc0a217c34','harbor','work','event','In review → Done.','human','2026-10-24T14:58:56.840339+00:00',NULL,6,NULL);
INSERT INTO "activity" VALUES(6,'91db571664284f01bd26a35922d6d14e','harbor','prepare-follow-up-6a9b533e','event','Task created in Backlog.','human','2026-10-24T14:58:56.880026+00:00',NULL,1,NULL);
INSERT INTO "activity" VALUES(7,'8917eb79557c4fd39a53d0b6ac2044ea','harbor','prepare-follow-up-6a9b533e','event','Input requested: Which audience?','human','2026-10-24T14:58:56.886381+00:00',NULL,NULL,'d07f55973f72493bbc617207fbf463b9');
INSERT INTO "activity" VALUES(8,'5d8be8bda8cd4d0dacb71a1940631622','a715a9b4be824b7289faabbec28b13b9','independent-report-4761dc90','event','Task created in Backlog.','human','2026-10-24T14:58:56.898722+00:00',NULL,1,NULL);
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
INSERT INTO "announcement_events" VALUES(1,'harbor:result:18e4593314814d099a4ff2ccf918a421','harbor','work','{"title": "harbor Workspace \u00b7 Work", "message": "Review current result", "href": "/workspaces/harbor/results/18e4593314814d099a4ff2ccf918a421", "exclude_clients": []}',1.792853936122049093e+09,'resolved',NULL,NULL);
INSERT INTO "announcement_events" VALUES(2,'harbor:result:18e4593314814d099a4ff2ccf918a421','harbor','work','{"title": "harbor Workspace \u00b7 Work", "message": "Review current result", "href": "/workspaces/harbor/results/18e4593314814d099a4ff2ccf918a421", "exclude_clients": []}',1.792853936841019869e+09,'resolved',NULL,NULL);
INSERT INTO "announcement_events" VALUES(3,'harbor:question:d07f55973f72493bbc617207fbf463b9','harbor','work','{"title": "harbor Workspace \u00b7 Which audience?", "message": "Needs your answer", "href": "/workspaces/harbor/requests/question/d07f55973f72493bbc617207fbf463b9", "exclude_clients": []}',1.792853936886823893e+09,'pending',NULL,NULL);
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
INSERT INTO "attention_sources" VALUES('harbor:question:d07f55973f72493bbc617207fbf463b9','harbor','open');
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
INSERT INTO "execution_local" VALUES('1de6b5fe293142298a5a0311fb3e7b65','{"runtime": "/fixture/schema-86/state/local-attempts/1de6b5fe293142298a5a0311fb3e7b65/runtime"}');
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
INSERT INTO "integration_settings" VALUES('harbor','{"project_id":"harbor","revision":2,"runtime":"local","target_branch":"integration","checks":["test -f result.txt"],"setup_commands":[],"setup_timeout_seconds":120,"check_timeout_seconds":60}');
CREATE TABLE integrations (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, project_id TEXT
    NOT NULL REFERENCES projects(id), run_id TEXT NOT NULL REFERENCES runs(id),
    status TEXT NOT NULL, data TEXT NOT NULL
);
INSERT INTO "integrations" VALUES('harbor',1,'3701c672ccc0456daf22e1fe050e62b1','harbor','1de6b5fe293142298a5a0311fb3e7b65','integrated','{"workspace_id":"harbor","task_id":"work","id":"3701c672ccc0456daf22e1fe050e62b1","project_id":"harbor","run_id":"1de6b5fe293142298a5a0311fb3e7b65","task_key":"T-1","revision":4,"settings_revision":2,"target_branch":"integration","target_before":"b19a0726cd8f2bc676f1b2897cf1792d97409eb0","checkout":{"path":"/fixture/schema-86/harbor","git_dir":"/fixture/schema-86/harbor/.git","device":16777230,"inode":160405953,"git_device":16777230,"git_inode":160405960},"candidate_commit":"4f65aee4cd4b515b1020b2bf0eaa4a7e5388144b","target_after":"4f65aee4cd4b515b1020b2bf0eaa4a7e5388144b","result_commit":"4f65aee4cd4b515b1020b2bf0eaa4a7e5388144b","status":"integrated","problem":null,"created_at":"2026-10-24T14:58:55.520888+00:00","completed_at":"2026-10-24T14:58:56.837865+00:00","validated_at":"2026-10-24T14:58:56.119524+00:00","apply_started_at":"2026-10-24T14:58:56.259043+00:00","checkout_verified_at":"2026-10-24T14:58:56.837919+00:00","author":"human","workspace":"/fixture/schema-86/state/integration-work/local-attempts/3701c672ccc0456daf22e1fe050e62b1/worktree","result_id":"18e4593314814d099a4ff2ccf918a421","purpose":"preparation","problem_code":null,"checks":[{"command":"test -f result.txt","exit_code":0,"output":"","truncated":false}],"setup_checks":[]}');
CREATE TABLE output_files (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL
    UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), run_id TEXT NOT
    NULL REFERENCES runs(id), data TEXT NOT NULL);
INSERT INTO "output_files" VALUES(1,'43ddb558a38a4965b853ba6a78129808','harbor','1de6b5fe293142298a5a0311fb3e7b65','{"id":"43ddb558a38a4965b853ba6a78129808","workspace_id":"harbor","project_id":"harbor","task_id":"work","task_key":"T-1","task_title":"Work","run_id":"1de6b5fe293142298a5a0311fb3e7b65","name":"brief.txt","description":"","media_type":"text/plain","size":26,"sha256":"a055ad4a45e1345ce71102c622047e5bc095a5a413fc26c82062806361e17668","created_at":"2026-10-24T14:58:56.892512+00:00","result_id":null,"result_version":null,"result_status":null}');
CREATE TABLE projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, description TEXT
    NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT
    NOT NULL, updated_by TEXT NOT NULL
, worker_documents TEXT NOT NULL DEFAULT '[]');
INSERT INTO "projects" VALUES('harbor','harbor','/fixture/schema-86/harbor','',1,'2026-10-24T14:58:54.848039+00:00','2026-10-24T14:58:54.848045+00:00','human','[]');
CREATE TABLE question_revisions (
 workspace_id TEXT NOT NULL, question_id TEXT NOT NULL, revision INTEGER NOT NULL, data
    TEXT NOT NULL, PRIMARY KEY(workspace_id, question_id, revision), FOREIGN KEY (workspace_id,
    question_id) REFERENCES questions(workspace_id, id)
);
INSERT INTO "question_revisions" VALUES('harbor','d07f55973f72493bbc617207fbf463b9',1,'{"id":"d07f55973f72493bbc617207fbf463b9","task_id":"prepare-follow-up-6a9b533e","affected_task_ids":[],"question":"Which audience?","context":"Choose the next pilot.","recommendation":"Internal team.","choices":[],"blocking_scope":null,"author":"human","workspace_id":"harbor","task_key":"T-2","affected_task_keys":[],"status":"open","revision":1,"answer":null,"answer_count":0,"origin_run_id":null,"origin_review_id":null,"continuation_run_id":null,"consumed_answer_revision":null,"correction_question_id":null,"decision":null,"applied_task_revision":null,"applied_workspace_revision":null,"applied_task_revisions":{},"created_at":"2026-10-24T14:58:56.886381+00:00","updated_at":"2026-10-24T14:58:56.886381+00:00","updated_by":"human"}');
CREATE TABLE questions (
 number INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    id TEXT NOT NULL, task_id TEXT, status TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(workspace_id,
    id), FOREIGN KEY (workspace_id, task_id) REFERENCES tasks(workspace_id, id)
);
INSERT INTO "questions" VALUES(1,'harbor','d07f55973f72493bbc617207fbf463b9','prepare-follow-up-6a9b533e','open','{"id":"d07f55973f72493bbc617207fbf463b9","task_id":"prepare-follow-up-6a9b533e","affected_task_ids":[],"question":"Which audience?","context":"Choose the next pilot.","recommendation":"Internal team.","choices":[],"blocking_scope":null,"author":"human","workspace_id":"harbor","task_key":"T-2","affected_task_keys":[],"status":"open","revision":1,"answer":null,"answer_count":0,"origin_run_id":null,"origin_review_id":null,"continuation_run_id":null,"consumed_answer_revision":null,"correction_question_id":null,"decision":null,"applied_task_revision":null,"applied_workspace_revision":null,"applied_task_revisions":{},"created_at":"2026-10-24T14:58:56.886381+00:00","updated_at":"2026-10-24T14:58:56.886381+00:00","updated_by":"human"}');
CREATE TABLE result_versions (
 number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, workspace_id TEXT
    NOT NULL REFERENCES workspaces(id), task_id TEXT NOT NULL, run_id TEXT NOT NULL
    REFERENCES runs(id), version INTEGER NOT NULL, status TEXT NOT NULL, data TEXT
    NOT NULL, UNIQUE(workspace_id, task_id, version), FOREIGN KEY(workspace_id, task_id)
    REFERENCES tasks(workspace_id, id)
);
INSERT INTO "result_versions" VALUES(1,'18e4593314814d099a4ff2ccf918a421','harbor','work','1de6b5fe293142298a5a0311fb3e7b65',1,'delivered','{"output_ids":[],"id":"18e4593314814d099a4ff2ccf918a421","workspace_id":"harbor","task_id":"work","task_key":"T-1","version":1,"revision":5,"run_id":"1de6b5fe293142298a5a0311fb3e7b65","completion":"code","source_commit":"4f65aee4cd4b515b1020b2bf0eaa4a7e5388144b","report":{"files":[],"summary":"Outcome","checks":"Worker-reported checks","limitations":"","outcome":"complete","remaining_work":""},"created_at":"2026-10-24T14:58:55.448474+00:00","status":"delivered","integration_id":"3701c672ccc0456daf22e1fe050e62b1","candidate_commit":"4f65aee4cd4b515b1020b2bf0eaa4a7e5388144b","target_branch":"integration","target_before":"b19a0726cd8f2bc676f1b2897cf1792d97409eb0","settings_revision":2,"approved_at":"2026-10-24T14:58:56.129460+00:00","approved_by":"human","approval_note":"","completed_at":"2026-10-24T14:58:56.840028+00:00","feedback":"","problem":null,"problem_code":null,"correction":null,"availability_id":null,"review_requested":true}');
CREATE TABLE review_attempts (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT
    NULL UNIQUE, review_id TEXT NOT NULL REFERENCES task_reviews(id), data TEXT NOT
    NULL);
CREATE TABLE reviewer_settings (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 entity_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(workspace_id,entity_id));
INSERT INTO "reviewer_settings" VALUES('harbor','','{"revision":2,"enabled":false,"selection":null}');
INSERT INTO "reviewer_settings" VALUES('a715a9b4be824b7289faabbec28b13b9','','{"revision":1,"enabled":true,"selection":{"harness":"codex","model":"test-model","effort":null,"mode":null,"fast":false}}');
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
INSERT INTO "runs" VALUES(1,'1de6b5fe293142298a5a0311fb3e7b65','harbor','work',NULL,'accepted','{"agent_settings":{"choice":{"harness":"codex","model":"fixture","effort":"low","mode":null,"fast":false},"source":"workspace","default_revision":3,"override_revision":1,"registration":{"executable":null,"config_directory":null,"harness":"codex","revision":1}},"applied_agent":null,"harness_launch":null,"id":"1de6b5fe293142298a5a0311fb3e7b65","workspace_id":"harbor","project_id":"harbor","task_id":"work","subtask_id":null,"task_key":"T-1","assembly_id":null,"subtask_revision":null,"purpose":"work","reply_id":null,"revision":5,"status":"accepted","environment_id":"1977cba257eb410cb6cda6855345948e","predecessor_id":null,"question_id":null,"input_question_id":null,"input_base_commit":null,"input_checkpoint":null,"correction":null,"next_correction":null,"runtime":"local","setup_commands":[],"setup_timeout_seconds":120,"setup_checks":[],"model":"fixture","effort":"low","agreement_revision":1,"base_commit":"b19a0726cd8f2bc676f1b2897cf1792d97409eb0","completion":"code","target_branch":"integration","result_commit":"4f65aee4cd4b515b1020b2bf0eaa4a7e5388144b","result":{"files":[],"summary":"Outcome","checks":"Worker-reported checks","limitations":"","outcome":"complete","remaining_work":""},"feedback":"","problem":null,"created_at":"2026-10-24T14:58:55.238097+00:00","started_at":"2026-10-24T14:58:55.242031+00:00","ended_at":"2026-10-24T14:58:55.447418+00:00","accepted_at":"2026-10-24T14:58:56.129460+00:00","accepted_by":"human","code_available":true,"checkpoint_commit":null,"check_settings_revision":2,"project_checks":["test -f result.txt"],"check_timeout_seconds":60,"project_check_results":[],"usage":{"input_tokens":null,"cached_input_tokens":null,"cache_write_input_tokens":null,"output_tokens":null,"reasoning_output_tokens":null,"total_tokens":null,"complete":false},"documents":[{"workspace_id":"harbor","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}],"excluded_files":[]}','{"agreement": "{\"task\": \"T-1\", \"task_revision\": 2, \"agreement_revision\": 1, \"completion\": \"code\", \"repository\": true, \"target_branch\": \"integration\", \"base_commit\": \"b19a0726cd8f2bc676f1b2897cf1792d97409eb0\", \"scope\": \"Frozen at claim; discoveries do not change intent.\"}", "description": "Deliver the result", "project": "", "criteria": "", "prerequisites": "[]", "feedback": "", "predecessor": "", "title": "T-1: Work", "correction": "", "validation": "{\"project_id\": \"harbor\", \"revision\": 2, \"runtime\": \"local\", \"target_branch\": \"integration\", \"checks\": [\"test -f result.txt\"], \"setup_commands\": [], \"setup_timeout_seconds\": 120, \"check_timeout_seconds\": 60, \"worker_checks_enabled\": false}", "files": "[]", "document:guidance": "", "documentation": "{\"policy\": \"Read required documents in full before acting. Reference documents are supporting material to consult when needed, not additional requirements. All selected revisions are frozen. Prose references do not expand tool scope. Ask a question if necessary material is missing or conflicts with the task.\", \"items\": [{\"workspace_id\": \"harbor\", \"id\": \"guidance\", \"revision\": 1, \"title\": \"Workspace guidance\", \"characters\": 0, \"bytes\": 0, \"sha256\": \"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\", \"purpose\": \"required\", \"section\": \"document:guidance\"}], \"characters\": 0, \"bytes\": 0, \"measurement\": \"Source sizes, not tokens. Harness usage reports actual tokens.\"}", "handoff": "", "questions": "[]", "project_checks": "Use run_project_checks for human-authorized saved commands. This section retains the latest output; read_context retrieves current pages.", "human_testing": "[]", "attempt_history": "{\"items\": [], \"earlier_omitted\": 0, \"source_index\": \"attempt_sources\", \"authority\": \"Historical observations, not current instructions.\"}", "attempt_sources": "{}", "prior_concerns": "[]"}');
CREATE VIRTUAL TABLE search_docs USING fts5(project_id UNINDEXED, entity UNINDEXED,
    identity UNINDEXED, revision UNINDEXED, kind UNINDEXED, task_id UNINDEXED,
    created_at UNINDEXED, title, body, workspace_id UNINDEXED, tokenize='unicode61');
INSERT INTO "search_docs" VALUES('harbor','project','harbor',1,'project',NULL,'2026-10-24T14:58:54.848045+00:00','harbor','','harbor');
INSERT INTO "search_docs" VALUES(NULL,'activity','76e27d6564474539801d09f352b054be',1,'event','work','2026-10-24T14:58:55.207804+00:00','event','Task created in Up next.','harbor');
INSERT INTO "search_docs" VALUES(NULL,'task','work',1,'task','work','2026-10-24T14:58:55.207804+00:00','Work','Deliver the result','harbor');
INSERT INTO "search_docs" VALUES(NULL,'activity','0df13b1ff5864a1798eb81ce3c1ed9bd',2,'event','work','2026-10-24T14:58:55.212070+00:00','event','Assignment checked and published.','harbor');
INSERT INTO "search_docs" VALUES(NULL,'task','work',2,'task','work','2026-10-24T14:58:55.212070+00:00','Work','Deliver the result','harbor');
INSERT INTO "search_docs" VALUES(NULL,'activity','ed3fd46c4ce74b17abe37f7acf227f34',3,'event','work','2026-10-24T14:58:55.237227+00:00','event','Up next → In progress.','harbor');
INSERT INTO "search_docs" VALUES(NULL,'task','work',3,'task','work','2026-10-24T14:58:55.237227+00:00','Work','Deliver the result','harbor');
INSERT INTO "search_docs" VALUES(NULL,'task','work',4,'task','work','2026-10-24T14:58:55.448939+00:00','Work','Deliver the result','harbor');
INSERT INTO "search_docs" VALUES(NULL,'activity','33dffc76ee4f43c98ef3a7ed2c4f31a8',4,'event','work','2026-10-24T14:58:56.121442+00:00','event','In progress → In review.','harbor');
INSERT INTO "search_docs" VALUES(NULL,'task','work',5,'task','work','2026-10-24T14:58:56.121442+00:00','Work','Deliver the result','harbor');
INSERT INTO "search_docs" VALUES(NULL,'result','18e4593314814d099a4ff2ccf918a421',5,'result','work','2026-10-24T14:58:55.448474+00:00','Outcome','Worker-reported checks


','harbor');
INSERT INTO "search_docs" VALUES(NULL,'activity','41e1f7fec93c456e8a0dccfc0a217c34',5,'event','work','2026-10-24T14:58:56.840339+00:00','event','In review → Done.','harbor');
INSERT INTO "search_docs" VALUES(NULL,'task','work',6,'task','work','2026-10-24T14:58:56.840339+00:00','Work','Deliver the result','harbor');
INSERT INTO "search_docs" VALUES(NULL,'activity','91db571664284f01bd26a35922d6d14e',6,'event','prepare-follow-up-6a9b533e','2026-10-24T14:58:56.880026+00:00','event','Task created in Backlog.','harbor');
INSERT INTO "search_docs" VALUES(NULL,'task','prepare-follow-up-6a9b533e',1,'task','prepare-follow-up-6a9b533e','2026-10-24T14:58:56.880026+00:00','Prepare follow-up','','harbor');
INSERT INTO "search_docs" VALUES(NULL,'task','prepare-follow-up-6a9b533e',2,'task','prepare-follow-up-6a9b533e','2026-10-24T14:58:56.883820+00:00','Prepare follow-up','','harbor');
INSERT INTO "search_docs" VALUES(NULL,'question','d07f55973f72493bbc617207fbf463b9',1,'question','prepare-follow-up-6a9b533e','2026-10-24T14:58:56.886381+00:00','Which audience?','Choose the next pilot.
Internal team.
','harbor');
INSERT INTO "search_docs" VALUES(NULL,'activity','8917eb79557c4fd39a53d0b6ac2044ea',7,'event','prepare-follow-up-6a9b533e','2026-10-24T14:58:56.886381+00:00','event','Input requested: Which audience?','harbor');
INSERT INTO "search_docs" VALUES(NULL,'activity','5d8be8bda8cd4d0dacb71a1940631622',8,'event','independent-report-4761dc90','2026-10-24T14:58:56.898722+00:00','event','Task created in Backlog.','a715a9b4be824b7289faabbec28b13b9');
INSERT INTO "search_docs" VALUES(NULL,'task','independent-report-4761dc90',1,'task','independent-report-4761dc90','2026-10-24T14:58:56.898722+00:00','Independent report','Summarize feedback.','a715a9b4be824b7289faabbec28b13b9');
CREATE TABLE storage_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), workspace_id
    TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision >= 0));
INSERT INTO "storage_metadata" VALUES(1,'b0a031cee86c2353b660ecea6a1fc4c5',27);
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
INSERT INTO "subtask_revisions" VALUES('harbor','prepare-follow-up-6a9b533e','check-the-small-screen-flow-e2d8f9b8',1,'{"workspace_id":"harbor","task_id":"prepare-follow-up-6a9b533e","id":"check-the-small-screen-flow-e2d8f9b8","key":"S-1","title":"Check the small-screen flow","body":"Retain the current release behavior.","completion":"code","dependencies":[],"revision":1,"created_at":"2026-10-24T14:58:56.883555+00:00","updated_at":"2026-10-24T14:58:56.883557+00:00","updated_by":"agent","contribution_run_id":null,"status":"pending"}');
CREATE TABLE subtasks (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, id TEXT NOT NULL,
 number INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(workspace_id,task_id,id),
 UNIQUE(workspace_id,task_id,number),
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id)
);
INSERT INTO "subtasks" VALUES('harbor','prepare-follow-up-6a9b533e','check-the-small-screen-flow-e2d8f9b8',1,'{"workspace_id":"harbor","task_id":"prepare-follow-up-6a9b533e","id":"check-the-small-screen-flow-e2d8f9b8","key":"S-1","title":"Check the small-screen flow","body":"Retain the current release behavior.","completion":"code","dependencies":[],"revision":1,"created_at":"2026-10-24T14:58:56.883555+00:00","updated_at":"2026-10-24T14:58:56.883557+00:00","updated_by":"agent","contribution_run_id":null,"status":"pending"}');
CREATE TABLE task_assemblies (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
 signature TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(workspace_id,task_id,signature),
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id)
);
CREATE TABLE task_dependencies (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, prerequisite_id TEXT NOT NULL,
 PRIMARY KEY(workspace_id,task_id,prerequisite_id), CHECK(task_id != prerequisite_id),
 FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id),
 FOREIGN KEY(workspace_id,prerequisite_id) REFERENCES tasks(workspace_id,id)
);
INSERT INTO "task_dependencies" VALUES('harbor','prepare-follow-up-6a9b533e','work');
CREATE TABLE task_replies (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, id TEXT PRIMARY KEY, created_at
    TEXT NOT NULL, data TEXT NOT NULL, FOREIGN KEY(workspace_id, task_id) REFERENCES
    tasks(workspace_id, id)
);
CREATE TABLE task_reviews (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL
    UNIQUE, workspace_id TEXT NOT NULL REFERENCES workspaces(id), task_id TEXT NOT
    NULL, result_id TEXT NOT NULL REFERENCES result_versions(id), binding TEXT NOT
    NULL, status TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(result_id, binding), FOREIGN
    KEY(workspace_id, task_id) REFERENCES tasks(workspace_id, id));
CREATE TABLE task_revisions (
 workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL, data
    TEXT NOT NULL, PRIMARY KEY (workspace_id, task_id, revision), FOREIGN KEY (workspace_id,
    task_id) REFERENCES tasks(workspace_id, id)
);
INSERT INTO "task_revisions" VALUES('harbor','work',1,'{"id":"work","title":"Work","body":"Deliver the result","revision":1,"created_at":"2026-10-24T14:58:55.207794+00:00","updated_at":"2026-10-24T14:58:55.207804+00:00","updated_by":"human","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-1","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('harbor','work',2,'{"id":"work","title":"Work","body":"Deliver the result","revision":2,"created_at":"2026-10-24T14:58:55.207794+00:00","updated_at":"2026-10-24T14:58:55.212070+00:00","updated_by":"human","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-24T14:58:55.212070+00:00","completion":"code","target_branch":"integration","documents":[{"workspace_id":"harbor","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-1","labels":[],"status":"up_next","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('harbor','work',3,'{"id":"work","title":"Work","body":"Deliver the result","revision":3,"created_at":"2026-10-24T14:58:55.207794+00:00","updated_at":"2026-10-24T14:58:55.237227+00:00","updated_by":"flowfield","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-24T14:58:55.212070+00:00","completion":"code","target_branch":"integration","documents":[{"workspace_id":"harbor","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-1","labels":[],"status":"in_progress","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":"Worker reserved for this task."}');
INSERT INTO "task_revisions" VALUES('harbor','work',4,'{"id":"work","title":"Work","body":"Deliver the result","revision":4,"created_at":"2026-10-24T14:58:55.207794+00:00","updated_at":"2026-10-24T14:58:55.448939+00:00","updated_by":"flowfield","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-24T14:58:55.212070+00:00","completion":"code","target_branch":"integration","documents":[{"workspace_id":"harbor","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-1","labels":[],"status":"in_progress","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":"Attempt 1de6b5fe submitted its result. Preparing the proposed result for review."}');
INSERT INTO "task_revisions" VALUES('harbor','work',5,'{"id":"work","title":"Work","body":"Deliver the result","revision":5,"created_at":"2026-10-24T14:58:55.207794+00:00","updated_at":"2026-10-24T14:58:56.121442+00:00","updated_by":"flowfield","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-24T14:58:55.212070+00:00","completion":"code","target_branch":"integration","documents":[{"workspace_id":"harbor","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-1","labels":[],"status":"in_review","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":"Ready for your delivery approval."}');
INSERT INTO "task_revisions" VALUES('harbor','work',6,'{"id":"work","title":"Work","body":"Deliver the result","revision":6,"created_at":"2026-10-24T14:58:55.207794+00:00","updated_at":"2026-10-24T14:58:56.840339+00:00","updated_by":"human","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-24T14:58:55.212070+00:00","completion":"code","target_branch":"integration","documents":[{"workspace_id":"harbor","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-1","labels":[],"status":"done","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":"Delivered result version 1 to integration."}');
INSERT INTO "task_revisions" VALUES('harbor','prepare-follow-up-6a9b533e',1,'{"id":"prepare-follow-up-6a9b533e","title":"Prepare follow-up","body":"","revision":1,"created_at":"2026-10-24T14:58:56.880024+00:00","updated_at":"2026-10-24T14:58:56.880026+00:00","updated_by":"human","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-2","labels":[],"status":"backlog","archived":false,"dependencies":["T-1"],"documents":[],"reconciliation_reason":null,"change_note":""}');
INSERT INTO "task_revisions" VALUES('harbor','prepare-follow-up-6a9b533e',2,'{"id":"prepare-follow-up-6a9b533e","title":"Prepare follow-up","body":"","revision":2,"created_at":"2026-10-24T14:58:56.880024+00:00","updated_at":"2026-10-24T14:58:56.883820+00:00","updated_by":"agent","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":2,"publication":null,"key":"T-2","labels":[],"status":"backlog","archived":false,"dependencies":["T-1"],"documents":[],"reconciliation_reason":null,"change_note":"Updated the task''s work plan."}');
INSERT INTO "task_revisions" VALUES('a715a9b4be824b7289faabbec28b13b9','independent-report-4761dc90',1,'{"id":"independent-report-4761dc90","title":"Independent report","body":"Summarize feedback.","revision":1,"created_at":"2026-10-24T14:58:56.898717+00:00","updated_at":"2026-10-24T14:58:56.898722+00:00","updated_by":"human","workspace_id":"a715a9b4be824b7289faabbec28b13b9","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-1","labels":[],"status":"backlog","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}');
CREATE TABLE tasks (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), id TEXT NOT NULL,
 position INTEGER NOT NULL, data TEXT NOT NULL, number INTEGER NOT NULL, key TEXT NOT NULL,
 project_id TEXT REFERENCES projects(id), scheduling_problem TEXT, PRIMARY KEY(workspace_id,id),
 UNIQUE(workspace_id,number), UNIQUE(workspace_id,key),
 FOREIGN KEY(workspace_id,project_id) REFERENCES workspace_projects(workspace_id,project_id)
);
INSERT INTO "tasks" VALUES('harbor','work',0,'{"id":"work","title":"Work","body":"Deliver the result","revision":6,"created_at":"2026-10-24T14:58:55.207794+00:00","updated_at":"2026-10-24T14:58:56.840339+00:00","updated_by":"human","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":{"agreement_revision":1,"task_revision":1,"author":"human","created_at":"2026-10-24T14:58:55.212070+00:00","completion":"code","target_branch":"integration","documents":[{"workspace_id":"harbor","id":"guidance","revision":1,"title":"Workspace guidance","characters":0,"bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","purpose":"required"}]},"key":"T-1","labels":[],"status":"done","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":"Delivered result version 1 to integration."}',1,'T-1','harbor',NULL);
INSERT INTO "tasks" VALUES('harbor','prepare-follow-up-6a9b533e',1,'{"id":"prepare-follow-up-6a9b533e","title":"Prepare follow-up","body":"","revision":2,"created_at":"2026-10-24T14:58:56.880024+00:00","updated_at":"2026-10-24T14:58:56.883820+00:00","updated_by":"agent","workspace_id":"harbor","project_id":"harbor","acceptance_criteria":"","input_files":[],"agreement_revision":2,"publication":null,"key":"T-2","labels":[],"status":"backlog","archived":false,"dependencies":["T-1"],"documents":[],"reconciliation_reason":null,"change_note":"Updated the task''s work plan."}',2,'T-2','harbor',NULL);
INSERT INTO "tasks" VALUES('a715a9b4be824b7289faabbec28b13b9','independent-report-4761dc90',0,'{"id":"independent-report-4761dc90","title":"Independent report","body":"Summarize feedback.","revision":1,"created_at":"2026-10-24T14:58:56.898717+00:00","updated_at":"2026-10-24T14:58:56.898722+00:00","updated_by":"human","workspace_id":"a715a9b4be824b7289faabbec28b13b9","project_id":null,"acceptance_criteria":"","input_files":[],"agreement_revision":1,"publication":null,"key":"T-1","labels":[],"status":"backlog","archived":false,"dependencies":[],"documents":[],"reconciliation_reason":null,"change_note":""}',1,'T-1',NULL,NULL);
CREATE TABLE update_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE worker_check_grants (project_id TEXT PRIMARY KEY REFERENCES projects(id),
    data TEXT NOT NULL);
CREATE TABLE workspace_coordinator_settings (workspace_id TEXT PRIMARY KEY REFERENCES
    workspaces(id), revision INTEGER NOT NULL, selection TEXT);
INSERT INTO "workspace_coordinator_settings" VALUES('harbor',1,'{"harness":"codex","model":"test-model","effort":null,"mode":null,"fast":false}');
INSERT INTO "workspace_coordinator_settings" VALUES('a715a9b4be824b7289faabbec28b13b9',1,'{"harness":"codex","model":"test-model","effort":null,"mode":null,"fast":false}');
CREATE TABLE workspace_document_revisions (workspace_id TEXT NOT NULL, id TEXT NOT
    NULL, revision INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at
    TEXT NOT NULL, author TEXT NOT NULL, restored_from INTEGER, PRIMARY KEY(workspace_id,
    id, revision), FOREIGN KEY(workspace_id, id) REFERENCES workspace_documents(workspace_id,
    id));
INSERT INTO "workspace_document_revisions" VALUES('harbor','guidance',1,'Workspace guidance','','2026-10-24T14:58:54.833781+00:00','human',NULL);
INSERT INTO "workspace_document_revisions" VALUES('harbor','ac4c574804b340a98818403c5e57bbec',1,'Release guidelines','Preserve release notes.','2026-10-24T14:58:56.875610+00:00','human',NULL);
INSERT INTO "workspace_document_revisions" VALUES('harbor','ac4c574804b340a98818403c5e57bbec',2,'Release guidelines','Preserve notes and exact source evidence.','2026-10-24T14:58:56.877869+00:00','human',NULL);
INSERT INTO "workspace_document_revisions" VALUES('a715a9b4be824b7289faabbec28b13b9','guidance',1,'Workspace guidance','','2026-10-24T14:58:56.894814+00:00','human',NULL);
CREATE TABLE workspace_documents (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    id TEXT NOT NULL, revision INTEGER NOT NULL, deleted_at TEXT, deleted_by TEXT,
    PRIMARY KEY(workspace_id, id));
INSERT INTO "workspace_documents" VALUES('harbor','guidance',1,NULL,NULL);
INSERT INTO "workspace_documents" VALUES('harbor','ac4c574804b340a98818403c5e57bbec',2,NULL,NULL);
INSERT INTO "workspace_documents" VALUES('a715a9b4be824b7289faabbec28b13b9','guidance',1,NULL,NULL);
CREATE TABLE workspace_labels (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    id TEXT NOT NULL, name TEXT NOT NULL, color TEXT NOT NULL, guidance TEXT NOT NULL,
    revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, deleted_at TEXT, PRIMARY KEY(workspace_id, id));
INSERT INTO "workspace_labels" VALUES('harbor','feature','Feature','blue','New or expanded product behavior.',1,'2026-10-24T14:58:54.833781+00:00','2026-10-24T14:58:54.833781+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('harbor','bug','Bug','orange','Correct behavior that does not match the intended outcome.',1,'2026-10-24T14:58:54.833781+00:00','2026-10-24T14:58:54.833781+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('harbor','maintenance','Maintenance','slate','Upkeep of existing code, dependencies or documentation.',1,'2026-10-24T14:58:54.833781+00:00','2026-10-24T14:58:54.833781+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('harbor','investigation','Investigation','violet','Answer a question or reduce uncertainty.',1,'2026-10-24T14:58:54.833781+00:00','2026-10-24T14:58:54.833781+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('a715a9b4be824b7289faabbec28b13b9','feature','Feature','blue','New or expanded product behavior.',1,'2026-10-24T14:58:56.894814+00:00','2026-10-24T14:58:56.894814+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('a715a9b4be824b7289faabbec28b13b9','bug','Bug','orange','Correct behavior that does not match the intended outcome.',1,'2026-10-24T14:58:56.894814+00:00','2026-10-24T14:58:56.894814+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('a715a9b4be824b7289faabbec28b13b9','maintenance','Maintenance','slate','Upkeep of existing code, dependencies or documentation.',1,'2026-10-24T14:58:56.894814+00:00','2026-10-24T14:58:56.894814+00:00','human',NULL);
INSERT INTO "workspace_labels" VALUES('a715a9b4be824b7289faabbec28b13b9','investigation','Investigation','violet','Answer a question or reduce uncertainty.',1,'2026-10-24T14:58:56.894814+00:00','2026-10-24T14:58:56.894814+00:00','human',NULL);
CREATE TABLE workspace_projects (project_id TEXT PRIMARY KEY REFERENCES projects(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id), UNIQUE(workspace_id,project_id));
INSERT INTO "workspace_projects" VALUES('harbor','harbor');
CREATE TABLE workspace_worker_settings (workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
    data TEXT NOT NULL);
INSERT INTO "workspace_worker_settings" VALUES('harbor','{"workspace_id":"harbor","revision":4,"selection":{"harness":"codex","model":"fixture","effort":"low","mode":null,"fast":false},"max_parallel":5,"auto_start_queue":false,"enabled":false,"problem":null}');
INSERT INTO "workspace_worker_settings" VALUES('a715a9b4be824b7289faabbec28b13b9','{"workspace_id":"a715a9b4be824b7289faabbec28b13b9","revision":1,"selection":{"harness":"codex","model":"test-model","effort":null,"mode":null,"fast":false},"max_parallel":5,"auto_start_queue":false,"enabled":false,"problem":null}');
CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT
    NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), created_at TEXT NOT
    NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL);
INSERT INTO "workspaces" VALUES('harbor','harbor Workspace','',1,'2026-10-24T14:58:54.833781+00:00','2026-10-24T14:58:54.833781+00:00','human');
INSERT INTO "workspaces" VALUES('a715a9b4be824b7289faabbec28b13b9','Second workspace','',1,'2026-10-24T14:58:56.894814+00:00','2026-10-24T14:58:56.894814+00:00','human');
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
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('activity',8);
INSERT INTO "sqlite_sequence" VALUES('runs',1);
INSERT INTO "sqlite_sequence" VALUES('result_versions',1);
INSERT INTO "sqlite_sequence" VALUES('integrations',1);
INSERT INTO "sqlite_sequence" VALUES('announcement_events',3);
INSERT INTO "sqlite_sequence" VALUES('questions',1);
INSERT INTO "sqlite_sequence" VALUES('output_files',1);
COMMIT;
PRAGMA user_version=86;
