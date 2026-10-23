"""Bounded orientation to current proposed results; never another workflow owner."""

import sqlite3
from typing import TYPE_CHECKING, Any

from flowfield.result_actions import result_action, reuse_action
from flowfield.result_models import ResultVersion

if TYPE_CHECKING:
    from flowfield.application import Workspace


def result_brief(
    workspace: "Workspace",
    db: sqlite3.Connection,
    workspace_id: str,
    task_id: str | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    rows = db.execute(
        "SELECT v.id,v.run_id,v.task_id,v.version,v.status,"
        "json_extract(v.data,'$.revision') AS revision,"
        "json_extract(v.data,'$.completion') AS completion,"
        "json_extract(v.data,'$.target_branch') AS target_branch,"
        "json_extract(v.data,'$.integration_id') AS integration_id,"
        "substr(json_extract(v.data,'$.report.summary'),1,300) AS outcome,"
        "substr(json_extract(v.data,'$.problem'),1,600) AS blocker,"
        "json_extract(v.data,'$.problem_code') AS problem_code,"
        "json_extract(r.data,'$.code_available') AS code_available,"
        "json_extract(r.data,'$.model') AS model,t.key AS task_key,"
        "latest.id AS current_run_id,latest.status AS current_run_status,count(*) OVER() AS total "
        "FROM result_versions v JOIN runs r ON r.id=v.run_id "
        "JOIN tasks t ON t.workspace_id=v.workspace_id AND t.id=v.task_id "
        "JOIN runs latest ON latest.workspace_id=v.workspace_id AND latest.task_id=v.task_id "
        "AND NOT EXISTS (SELECT 1 FROM runs n WHERE n.workspace_id=latest.workspace_id "
        "AND n.task_id=latest.task_id AND n.number>latest.number) "
        "WHERE v.workspace_id=? AND (? IS NULL OR v.task_id=?) "
        "AND NOT json_extract(t.data,'$.archived') "
        "AND NOT EXISTS (SELECT 1 FROM result_versions n WHERE n.workspace_id=v.workspace_id "
        "AND n.task_id=v.task_id AND n.version>v.version) "
        "ORDER BY CASE WHEN v.status IN ('blocked','stale') THEN 0 "
        "WHEN v.status='ready' THEN 1 WHEN v.status='delivered' THEN 3 ELSE 2 END,"
        "v.number DESC LIMIT ?",
        (workspace_id, task_id, task_id, limit),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item.pop("total")
        item["current"] = item["run_id"] == item["current_run_id"]
        saved = db.execute("SELECT data FROM result_versions WHERE id=?", (item["id"],)).fetchone()
        version = ResultVersion.model_validate_json(saved[0])
        action = result_action(workspace, db, version)
        reuse = reuse_action(workspace, db, version)
        item["reuse_action"] = reuse.model_dump() if reuse else None
        item["owner"], item["next_action"] = action.owner, action.action
        item["action_label"], item["action_reason"] = action.label, action.reason
        item["outcome_excerpt"] = item.pop("outcome")
        if item["blocker"]:
            item["blocker"] = item["blocker"].split("\n", 1)[0]
        item["source"] = {
            "read": "get_result",
            "result_id": item["id"],
            "revision": item["revision"],
        }
        items.append(item)
    total = rows[0]["total"] if rows else 0
    return {"items": items, "count": total, "omitted_count": total - len(items)}
