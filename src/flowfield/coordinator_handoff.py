"""Frozen history boundaries; native context headroom decides inline selection."""

import json
import sqlite3
from typing import Any

from flowfield.activity_text import OMISSION, retain
from flowfield.attachments import REFERENCE
from flowfield.coordinator_models import CoordinatorTurn
from flowfield.reads import iter_coordinator_exchanges, size


def attachment_identities(project: str, text: str) -> list[dict[str, str]]:
    return [
        {"id": identity, "href": f"/api/projects/{project}/attachments/{identity}"}
        for owner, identity in dict.fromkeys(REFERENCE.findall(text))
        if owner == project
    ]


def freeze(db: sqlite3.Connection, turn: CoordinatorTurn) -> dict[str, Any]:
    # Previous turns are closed before reserve accepts another message. Their public
    # text cannot change: the store rejects late writes. Freeze a boundary into those
    # canonical sources instead of copying a growing transcript on every native resume.
    count, latest = db.execute(
        "SELECT count(*),max(number) FROM coordinator_turns WHERE project_id=? AND number<?",
        (turn.project_id, turn.number),
    ).fetchone()
    return {
        "version": 2,
        "source_before": turn.number,
        "source_latest": latest,
        "source_count": count,
        "history_is_partial": bool(count),
        "recent_conversation": [],
        "current_attachments": attachment_identities(turn.project_id, turn.text),
        "earlier_attachment_contents": "not_transferred; reattach relevant files",
        "older_history": {"tool": "get_coordinator_history", "before": turn.number},
    }


def select(
    db: sqlite3.Connection, project: str, snapshot: dict[str, Any], budget: int | None
) -> dict[str, Any]:
    if snapshot["version"] == 1:
        return snapshot  # Preserve already accepted historical snapshots verbatim.
    result = {**snapshot, "recent_conversation": [], "history_mode": "retrieve"}
    if budget is None or budget <= size(result):
        return result
    result["history_mode"] = "inline"
    result["history_is_partial"] = False
    for source in iter_coordinator_exchanges(db, project, snapshot["source_before"]):
        entry = {
            key: source[key]
            for key in (
                "id",
                "number",
                "revision",
                "human",
                "coordinator",
                "status",
                "output_omitted",
            )
        }
        entry.update(
            selected_task=json.dumps(source["task_context"]) if source["task_context"] else "",
            attachments=attachment_identities(project, source["human"]),
            text_sources={
                field: {
                    "tool": "get_text",
                    "resource": "coordinator",
                    "identity": source["id"],
                    "field": field,
                    "revision": source["revision"],
                }
                for field in ("human", "coordinator")
            },
        )
        existing = result["recent_conversation"]
        candidate = {**result, "recent_conversation": [entry, *existing]}
        if size(candidate) > budget:
            result["history_is_partial"] = True
            if existing:
                break
            original = entry.copy()
            low, high = 0, max(len(entry["human"]), len(entry["coordinator"]))
            fitting = None
            while low <= high:
                limit = (low + high) // 2
                for field in ("human", "coordinator"):
                    entry[field] = retain(original[field], limit) if limit > len(OMISSION) else ""
                entry["abridged_fields"] = [
                    field for field in ("human", "coordinator") if entry[field] != original[field]
                ]
                if size({**result, "recent_conversation": [entry]}) <= budget:
                    fitting = entry.copy()
                    low = limit + 1
                else:
                    high = limit - 1
            if fitting:
                existing.append(fitting)
            break
        existing.insert(0, entry)
        result["history_is_partial"] |= bool(source["output_omitted"] or entry["attachments"])
    result["history_is_partial"] |= len(result["recent_conversation"]) < snapshot["source_count"]
    if not result["recent_conversation"]:
        result["history_mode"] = "retrieve"
    return result
