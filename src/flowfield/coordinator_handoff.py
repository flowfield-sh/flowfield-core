"""Immutable bounded public source selection, made inside message reservation."""

import json
import sqlite3
from typing import Any

from flowfield.activity_text import retain
from flowfield.attachments import REFERENCE
from flowfield.coordinator_models import CoordinatorTurn
from flowfield.reads import coordinator_exchanges, size

HANDOFF_BYTES = 48_000
HANDOFF_EXCHANGES = 8


def attachment_identities(project: str, text: str) -> list[dict[str, str]]:
    return [
        {"id": identity, "href": f"/api/projects/{project}/attachments/{identity}"}
        for owner, identity in dict.fromkeys(REFERENCE.findall(text))
        if owner == project
    ]


def freeze(db: sqlite3.Connection, turn: CoordinatorTurn) -> dict[str, Any]:
    # Read the same transaction that accepts this message. No later launch, restart or
    # retry may rebuild these sources from a changed chat or task selection.
    sources = coordinator_exchanges(db, turn.project_id, turn.number, HANDOFF_EXCHANGES + 1)
    result: dict[str, Any] = {
        "version": 1,
        "source_before": turn.number,
        "source_latest": sources[0]["number"] if sources else None,
        "history_is_partial": len(sources) > HANDOFF_EXCHANGES,
        "recent_conversation": [],
        "current_attachments": attachment_identities(turn.project_id, turn.text),
        "earlier_attachment_contents": "not_transferred; reattach relevant files",
        "older_history": {"tool": "get_coordinator_history", "before": turn.number},
    }
    for source in sources[:HANDOFF_EXCHANGES]:
        entry: dict[str, Any] = {
            "id": source["id"],
            "number": source["number"],
            "revision": source["revision"],
            "human": source["human"],
            "coordinator": source["coordinator"],
            "status": source["status"],
            "selected_task": json.dumps(source["task_context"]) if source["task_context"] else "",
            "output_omitted": source["output_omitted"],
            "attachments": attachment_identities(turn.project_id, source["human"]),
            "text_sources": {
                field: {
                    "tool": "get_text",
                    "resource": "coordinator",
                    "identity": source["id"],
                    "field": field,
                    "revision": source["revision"],
                }
                for field in ("human", "coordinator")
            },
        }
        existing = result["recent_conversation"]
        candidate = {**result, "recent_conversation": [entry, *existing]}
        if size(candidate) > HANDOFF_BYTES:
            result["history_is_partial"] = True
            if existing:
                break
            original = dict(entry)
            limit = max(len(entry["human"]), len(entry["coordinator"]))
            while size(candidate) > HANDOFF_BYTES:
                limit //= 2
                for field in ("human", "coordinator"):
                    entry[field] = retain(original[field], limit)
                entry["abridged_fields"] = [
                    field for field in ("human", "coordinator") if entry[field] != original[field]
                ]
                candidate = {**result, "recent_conversation": [entry]}
        result["recent_conversation"].insert(0, entry)
        result["history_is_partial"] |= bool(source["output_omitted"] or entry["attachments"])
    return result
