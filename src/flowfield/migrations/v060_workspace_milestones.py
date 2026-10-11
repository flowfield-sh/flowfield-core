"""Workspace milestone identity and a single search index with explicit owners."""

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    if db.execute(
        "SELECT 1 FROM milestones m LEFT JOIN workspace_projects p ON p.project_id=m.project_id "
        "WHERE p.workspace_id IS NULL LIMIT 1"
    ).fetchone():
        raise ValueError("Milestone has no workspace membership")
    for field in ("id", "number", "key"):
        if db.execute(
            f"SELECT 1 FROM milestones m JOIN workspace_projects p ON p.project_id=m.project_id "
            f"GROUP BY p.workspace_id,m.{field} HAVING count(*)>1 LIMIT 1"
        ).fetchone():
            raise ValueError("Cannot merge colliding milestone identities or keys")
    db.execute("CREATE TEMP TABLE old_milestones AS SELECT * FROM milestones")
    db.execute("DROP TABLE milestones")
    db.execute(
        "CREATE TABLE milestones (workspace_id TEXT NOT NULL REFERENCES workspaces(id), "
        "id TEXT NOT NULL, data TEXT NOT NULL, number INTEGER NOT NULL, key TEXT NOT NULL, "
        "PRIMARY KEY(workspace_id,id), UNIQUE(workspace_id,number), UNIQUE(workspace_id,key))"
    )
    db.execute(
        "INSERT INTO milestones SELECT p.workspace_id,m.id,"
        "json_set(json_remove(m.data,'$.project_id'),'$.workspace_id',p.workspace_id),"
        "m.number,m.key FROM old_milestones m "
        "JOIN workspace_projects p ON p.project_id=m.project_id"
    )
    db.execute("DROP TABLE old_milestones")

    # The FTS row remains one source, even when a milestone appears in several projects.
    # Preserve rowids/cursors; project evidence gains its workspace without losing its repo.
    db.execute("CREATE TEMP TABLE old_search_docs AS SELECT rowid AS cursor,* FROM search_docs")
    for name in (
        "search_projects_insert",
        "search_projects_update",
        "search_task_revisions_insert",
        "search_question_revisions_insert",
        "search_activity_insert",
        "search_result_versions_insert",
        "search_result_versions_update",
    ):
        db.execute(f"DROP TRIGGER {name}")
    db.execute("DROP TABLE search_docs")
    db.execute(
        "CREATE VIRTUAL TABLE search_docs USING fts5("
        "project_id UNINDEXED,entity UNINDEXED,identity UNINDEXED,revision UNINDEXED,"
        "kind UNINDEXED,task_id UNINDEXED,created_at UNINDEXED,title,body,workspace_id UNINDEXED,"
        "tokenize='unicode61')"
    )
    db.execute(
        "INSERT INTO search_docs(rowid,project_id,entity,identity,revision,kind,task_id,"
        "created_at,title,body,workspace_id) SELECT d.cursor,"
        "CASE WHEN d.entity!='milestone' THEN d.project_id END,d.entity,d.identity,d.revision,"
        "d.kind,d.task_id,d.created_at,d.title,d.body,p.workspace_id "
        "FROM old_search_docs d LEFT JOIN workspace_projects p ON p.project_id=d.project_id"
    )
    db.execute("DROP TABLE old_search_docs")

    for event in ("INSERT", "UPDATE"):
        delete = (
            "DELETE FROM search_docs WHERE project_id=new.id AND entity='project';"
            if event == "UPDATE"
            else ""
        )
        db.execute(
            f"CREATE TRIGGER search_projects_{event.lower()} AFTER {event} ON projects BEGIN "
            + delete
            + "INSERT INTO search_docs VALUES (new.id,'project',new.id,new.revision,'project',NULL,"
            "new.updated_at,new.name,new.description,"
            "(SELECT workspace_id FROM workspace_projects WHERE project_id=new.id)); END"
        )
    db.execute(
        "CREATE TRIGGER search_project_membership AFTER INSERT ON workspace_projects BEGIN "
        "UPDATE search_docs SET workspace_id=new.workspace_id WHERE project_id=new.project_id; END"
    )
    sources = (
        (
            "task_revisions",
            "task",
            "new.task_id,new.revision,json_extract(new.data,'$.task_type'),"
            "new.task_id,json_extract(new.data,'$.updated_at'),json_extract(new.data,'$.title'),"
            "json_extract(new.data,'$.body')",
            False,
        ),
        (
            "question_revisions",
            "question",
            "new.question_id,new.revision,'question',"
            "json_extract(new.data,'$.task_id'),json_extract(new.data,'$.updated_at'),"
            "json_extract(new.data,'$.question'),coalesce(json_extract(new.data,'$.context'),'')"
            "||char(10)||coalesce(json_extract(new.data,'$.recommendation'),'')||char(10)||"
            "coalesce(json_extract(new.data,'$.answer'),'')",
            False,
        ),
        (
            "activity",
            "activity",
            "new.id,new.sequence,new.kind,new.task_id,new.created_at,new.kind,new.body",
            False,
        ),
        (
            "milestones",
            "milestone",
            "new.id,json_extract(new.data,'$.revision'),'milestone',NULL,"
            "json_extract(new.data,'$.updated_at'),json_extract(new.data,'$.title'),"
            "json_extract(new.data,'$.body')",
            True,
        ),
        (
            "result_versions",
            "result",
            "new.id,json_extract(new.data,'$.revision'),'result',"
            "new.task_id,json_extract(new.data,'$.created_at'),"
            "json_extract(new.data,'$.report.summary'),coalesce(json_extract(new.data,'$.report.checks'),'')"
            "||char(10)||coalesce(json_extract(new.data,'$.report.limitations'),'')||char(10)||"
            "coalesce(json_extract(new.data,'$.feedback'),'')||char(10)||"
            "coalesce(json_extract(new.data,'$.problem'),'')",
            True,
        ),
    )
    for table, entity, values, mutable in sources:
        owner = (
            "new.workspace_id"
            if entity == "milestone"
            else ("(SELECT workspace_id FROM workspace_projects WHERE project_id=new.project_id)")
        )
        project = "NULL" if entity == "milestone" else "new.project_id"
        for event in ("INSERT", "UPDATE") if mutable else ("INSERT",):
            column = "workspace_id" if entity == "milestone" else "project_id"
            delete = (
                f"DELETE FROM search_docs WHERE {column}=new.{column} AND entity='{entity}' "
                "AND identity=new.id;"
                if event == "UPDATE"
                else ""
            )
            db.execute(
                f"CREATE TRIGGER search_{table}_{event.lower()} AFTER {event} ON {table} BEGIN "
                + delete
                + f"INSERT INTO search_docs VALUES ({project},'{entity}',{values},{owner}); END"
            )
