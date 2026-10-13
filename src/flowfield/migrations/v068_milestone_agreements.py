"""Retain milestone agreements and bind existing effective sources at upgrade."""

import json
import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE milestone_revisions (workspace_id TEXT NOT NULL, id TEXT NOT NULL, "
        "revision INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(workspace_id,id,revision), "
        "FOREIGN KEY(workspace_id,id) REFERENCES milestones(workspace_id,id))"
    )
    db.execute(
        "INSERT INTO milestone_revisions SELECT workspace_id,id,"
        "json_extract(data,'$.revision'),data FROM milestones"
    )
    for event in ("INSERT", "UPDATE"):
        condition = (
            " WHEN json_extract(new.data,'$.revision')!=json_extract(old.data,'$.revision')"
            if event == "UPDATE"
            else ""
        )
        db.execute(
            f"CREATE TRIGGER milestone_revisions_{event.lower()} AFTER {event} ON milestones"
            + condition
            + " BEGIN INSERT INTO milestone_revisions VALUES "
            "(new.workspace_id,new.id,json_extract(new.data,'$.revision'),new.data); END"
        )
        db.execute(f"DROP TRIGGER search_milestones_{event.lower()}")
        delete = (
            "DELETE FROM search_docs WHERE workspace_id=new.workspace_id "
            "AND entity='milestone' AND identity=new.id;"
            if event == "UPDATE"
            else ""
        )
        db.execute(
            f"CREATE TRIGGER search_milestones_{event.lower()} AFTER {event} ON milestones BEGIN "
            + delete
            + "INSERT INTO search_docs VALUES (NULL,'milestone',new.id,"
            "json_extract(new.data,'$.revision'),'milestone',NULL,"
            "json_extract(new.data,'$.updated_at'),json_extract(new.data,'$.title'),"
            "json_extract(new.data,'$.body')||char(10)||"
            "coalesce(json_extract(new.data,'$.acceptance_criteria'),''),new.workspace_id); END"
        )
    # Previous releases resolved milestone text at launch. Freeze that effective source
    # now, explicitly attributed to the upgrade rather than a past preparation. Never
    # change historical runs/assignments or manufacture earlier milestone revisions.
    for row in db.execute(
        "SELECT t.project_id,t.id,t.data,m.data AS milestone FROM tasks t "
        "JOIN workspace_projects w ON w.project_id=t.project_id "
        "JOIN milestones m ON m.workspace_id=w.workspace_id "
        "AND m.id=json_extract(t.data,'$.milestone_id') "
        "WHERE json_type(t.data,'$.publication')='object' "
        "AND json_extract(t.data,'$.status')!='done'"
    ).fetchall():
        task = json.loads(row[2])
        milestone = json.loads(row[3])
        binding = {
            key: milestone[key] for key in ("workspace_id", "id", "key", "title", "revision")
        }
        binding["origin"] = "upgrade"
        task["publication"]["milestone"] = binding
        data = json.dumps(task, ensure_ascii=False)
        db.execute("UPDATE tasks SET data=? WHERE project_id=? AND id=?", (data, row[0], row[1]))
        db.execute(
            "UPDATE task_revisions SET data=? WHERE project_id=? AND task_id=? AND revision=?",
            (data, row[0], row[1], task["revision"]),
        )
