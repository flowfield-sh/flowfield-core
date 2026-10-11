"""Use immutable, unambiguous task keys for workspace-wide dependency graphs."""

import json
import sqlite3


def apply(db: sqlite3.Connection) -> None:
    # Prefixes are service-unique and locked after the first task. Every prior edge
    # names a project-local ID; convert its representation without changing its target,
    # agreement revision, completion or any frozen worker assignment.
    keys = {(row[0], row[1]): row[2] for row in db.execute("SELECT project_id,id,key FROM tasks")}
    for table, identity in (("tasks", "id"), ("task_revisions", "task_id")):
        for row in db.execute(
            f"SELECT project_id,{identity},data FROM {table} "
            "WHERE json_array_length(data,'$.dependencies')>0"
        ).fetchall():
            value = json.loads(row[2])
            try:
                dependencies = sorted({keys[(row[0], old)] for old in value["dependencies"]})
            except KeyError as error:
                raise ValueError(
                    "Saved dependency does not identify its original project task"
                ) from error
            where = " AND revision=?" if table == "task_revisions" else ""
            args = [json.dumps(dependencies), row[0], row[1]]
            if table == "task_revisions":
                args.append(value["revision"])
            db.execute(
                f"UPDATE {table} SET data=json_set(data,'$.dependencies',json(?)) "
                f"WHERE project_id=? AND {identity}=?{where}",
                args,
            )
