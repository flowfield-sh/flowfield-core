"""Bounded numeric evidence at application boundaries, never a transcript or token estimate."""

import hashlib
import json
import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from flowfield.application import now

if TYPE_CHECKING:
    from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.usage import Usage
from flowfield.usage_models import Source

MAX_EVENTS = 2048


def encoded(value: Any) -> bytes:
    return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)).encode()


class ContextMeasurements:
    def __init__(self, store: "Workspace", workspace: str, source: Source, identity: str):
        self.store, self.workspace, self.source, self.identity = store, workspace, source, identity

    def record(self, kind: str, size: int, data: dict[str, Any]) -> None:
        try:
            self._record(kind, size, data)
        except (ApplicationError, sqlite3.Error):
            # Measurement must never turn a successful mutation into a retryable failure.
            logging.getLogger(__name__).warning("Context measurement unavailable")

    def _record(self, kind: str, size: int, data: dict[str, Any]) -> None:
        with self.store.connection(write=True, notify=False) as db:
            owner = Usage.owner(db, self.workspace, self.source, self.identity)["number"]
            db.execute("INSERT OR IGNORE INTO usage_context_state(execution) VALUES(?)", (owner,))
            count = db.execute(
                "SELECT count(*) FROM usage_context WHERE execution=?", (owner,)
            ).fetchone()[0]
            if count >= MAX_EVENTS:
                db.execute(
                    "UPDATE usage_context_state SET dropped=dropped+1 WHERE execution=?", (owner,)
                )
                return
            db.execute(
                "INSERT INTO usage_context(execution,received_at,kind,bytes,data) "
                "VALUES(?,?,?,?,?)",
                (owner, now(), kind, size, json.dumps(data)),
            )

    def native(self, kind: str, size: int, representation: str) -> None:
        self.record(kind, size, {"representation": representation})

    def prompt(self, text: str, *, resumed: bool = False, attachments: int = 0) -> str:
        parts = {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                parts = {key: len(encoded(value)) for key, value in list(parsed.items())[:32]}
        except ValueError:
            pass
        self.record(
            "prompt",
            len(encoded(text)),
            {"parts_bytes": parts, "resumed": resumed, "attachments": attachments},
        )
        return text

    def tools(self, tools: list[Any]) -> None:
        value = [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
        self.record("tool_definitions", len(encoded(value)), {"count": len(value)})

    def result(
        self, name: str, arguments: dict[str, Any], result: Any, *, read_only: bool = False
    ) -> None:
        content = [item.model_dump(mode="json", exclude_none=True) for item in result.content]
        metadata: dict[str, Any] = {
            "tool": name,
            "read_only": read_only,
            "request_sha256": hashlib.sha256(
                json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
            "structured_bytes": len(encoded(result.structuredContent))
            if result.structuredContent is not None
            else 0,
            "is_error": bool(result.isError),
        }
        # Successful navigation names an authorized source; never retain queries or arbitrary args.
        if not result.isError and name in {
            "read_context",
            "get_document_text",
            "get_coordinator_guide",
        }:
            metadata["source"] = {
                key: arguments[key]
                for key in ("section", "document_id")
                if isinstance(arguments.get(key), str) and len(arguments[key]) <= 200
            }
            metadata["requested_range"] = {
                key: arguments[key]
                for key in ("offset", "end", "limit", "revision")
                if type(arguments.get(key)) is int
            }
            metadata["mode"] = arguments.get(
                "mode", "outline" if name == "get_coordinator_guide" else "read"
            )
        # Only a bounded location description survives. Never retain snippets or paths.
        for item in result.content:
            if item.type == "text":
                try:
                    page = json.loads(item.text)
                except ValueError:
                    continue
                if isinstance(page, dict):
                    metadata["range"] = {
                        key: page[key]
                        for key in (
                            "offset",
                            "end_offset",
                            "total_chars",
                            "next_offset",
                            "revision",
                        )
                        if type(page.get(key)) is int
                        or (key == "next_offset" and key in page and page[key] is None)
                    }
                    if isinstance(page.get("sha256"), str) and len(page["sha256"]) == 64:
                        metadata["source_sha256"] = page["sha256"]
                    break
        self.record("tool_result", len(encoded(content)), metadata)

    def read(self, after: int = 0, limit: int = 50) -> dict[str, Any]:
        if after < 0 or not 1 <= limit <= 100:
            raise ApplicationError("invalid_request", "Use a nonnegative cursor and limit 1–100.")
        with self.store.connection() as db:
            owner = Usage.owner(db, self.workspace, self.source, self.identity)["number"]
            state = db.execute(
                "SELECT dropped FROM usage_context_state WHERE execution=?", (owner,)
            ).fetchone()
            rows = db.execute(
                "SELECT * FROM usage_context WHERE execution=? AND number>? ORDER BY number"
                " LIMIT ?",
                (owner, after, limit + 1),
            ).fetchall()
            totals = [
                dict(row)
                for row in db.execute(
                    "SELECT kind,count(*) events,sum(bytes) bytes FROM usage_context WHERE "
                    "execution=? GROUP BY kind",
                    (owner,),
                )
            ]
            repeats = db.execute(
                "SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM usage_context "
                "WHERE execution=? AND kind='tool_result' "
                "AND json_extract(data,'$.read_only')=1 GROUP BY "
                "json_extract(data,'$.request_sha256'),json_extract(data,'$.tool'))",
                (owner,),
            ).fetchone()[0]
            failures = db.execute(
                "SELECT count(*) FROM usage_context WHERE execution=? AND kind='tool_result' "
                "AND json_extract(data,'$.is_error')=1",
                (owner,),
            ).fetchone()[0]
        return {
            "source": self.source,
            "source_id": self.identity,
            "coverage": "unavailable" if state is None else "partial" if state[0] else "recorded",
            "basis": "Reported payload bytes; not model tokens. Native instructions,"
            " token serialization, compaction and attachments are not attributed. "
            "Tool definitions describe server inventory; harnesses may load only a subset. "
            "Native tool outputs may overlap Flowfield results; do not add them together.",
            "dropped_events": state[0] if state else 0,
            "totals": totals,
            "repeated_read_requests": repeats,
            "failed_tool_requests": failures,
            "items": [
                {
                    "number": r["number"],
                    "received_at": r["received_at"],
                    "kind": r["kind"],
                    "bytes": r["bytes"],
                    **json.loads(r["data"]),
                }
                for r in rows[:limit]
            ],
            "next_after": rows[limit - 1]["number"] if len(rows) > limit else None,
        }
