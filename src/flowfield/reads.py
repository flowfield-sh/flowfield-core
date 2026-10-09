"""Bounded context reads shared by MCP and CLI, separate from the browser snapshot."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlencode

from flowfield.activity import EntryKind
from flowfield.application import STATUSES, TaskRevision, Workspace, now
from flowfield.errors import ApplicationError
from flowfield.input_delivery import question_delivery
from flowfield.publication import (
    preparation_issue,
    publication_status,
    readiness,
    stages_need_reconciliation,
)
from flowfield.questions import Questions, blocking_questions
from flowfield.result_brief import result_brief

PAGE_BYTES = 24_000
DEFAULT_LIMIT = 20
MAX_LIMIT = 50


def size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def clip(value: str, budget: int) -> str:
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if size(value[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return value[:low]


def excerpt(item: dict[str, Any], budget: int = 2000) -> dict[str, Any]:
    result = dict(item)
    truncated = {}
    for key, value in result.items():
        if isinstance(value, str) and size(value) > budget:
            result[key] = clip(value, budget)
            truncated[key] = {
                "total_characters": len(value),
                "returned_characters": len(result[key]),
            }
    if truncated:
        result["truncated_fields"] = truncated
    return result


def receipt(item: dict[str, Any]) -> dict[str, Any]:
    """A mutation confirms the saved identity/revision without echoing content or history."""
    keys = (
        "id",
        "key",
        "project_id",
        "task_id",
        "task_revision",
        "task_key",
        "question_id",
        "applied_task_revision",
        "applied_project_revision",
        "applied_task_revisions",
        "title",
        "name",
        "revision",
        "status",
        "readiness",
        "publication_status",
        "agreement_revision",
        "archived",
        "updated_at",
        "updated_by",
        "author",
        "sequence",
        "kind",
        "created_at",
        "supersedes",
        "superseded_by",
        "task_prefix",
        "path",
        "version",
        "completion",
        "candidate_commit",
        "source_commit",
        "integration_id",
        "availability_id",
        "target_branch",
        "approved_at",
        "completed_at",
        "message",
    )
    return {"saved": True, **excerpt({k: item[k] for k in keys if k in item}, 500)}


def validate_page(limit: int, cursor: int | str | None = None) -> None:
    if not 1 <= limit <= MAX_LIMIT or (isinstance(cursor, int) and cursor < 1):
        raise ApplicationError("invalid_request", "Limit must be 1–50; cursor must be positive.")


def page(
    items: list[dict[str, Any]], limit: int, cursor_field: str, *, budget: int = PAGE_BYTES
) -> dict[str, Any]:
    result: list[dict[str, Any]] = []
    for item in items[:limit]:
        if result and size({"items": [*result, item], "next_cursor": item[cursor_field]}) > budget:
            break
        result.append(item)
    return {
        "items": result,
        "next_cursor": result[-1][cursor_field] if len(items) > len(result) else None,
    }


def coordinator_output_omitted(activity: dict[str, Any]) -> bool:
    # A recorded delivery gap may have lost prose even when surviving entries fit.
    return bool(
        activity.get("omitted", False)
        or any(
            entry.get("omitted", False)
            for entry in activity["items"]
            if entry["kind"] == "agent" or entry["key"] == "stream-gap"
        )
    )


def coordinator_exchanges(
    db: sqlite3.Connection, project: str, before: int, limit: int
) -> list[dict[str, Any]]:
    """Retained public sources; callers bound excerpts/prompt bytes separately."""
    rows = db.execute(
        "SELECT id,number,status,json_extract(data,'$.created_at') created_at, "
        "json_extract(data,'$.text') human,json_extract(data,'$.activity') activity, "
        "json_extract(data,'$.task_context') task_context FROM coordinator_turns "
        "WHERE project_id=? AND number<? ORDER BY number DESC LIMIT ?",
        (project, before, limit),
    ).fetchall()
    items = []
    for row in rows:
        activity = json.loads(row["activity"])
        prose = [entry for entry in activity["items"] if entry["kind"] == "agent"]
        items.append(
            {
                "id": row["id"],
                "number": row["number"],
                "status": row["status"],
                "created_at": row["created_at"],
                "revision": activity.get("revision", 0),
                "human": row["human"],
                "coordinator": "\n\n".join(entry["text"] for entry in prose),
                "output_omitted": coordinator_output_omitted(activity),
                "task_context": json.loads(row["task_context"]) if row["task_context"] else None,
            }
        )
    return items


def task_metadata(
    db: sqlite3.Connection, project_id: str
) -> tuple[dict[str, TaskRevision], dict[str, int]]:
    # Read graph/current metadata only; never hydrate descriptions or revision histories.
    rows = db.execute(
        "SELECT id, position, json_set(data, '$.body', '', "
        "'$.change_note', '') AS data FROM tasks WHERE project_id=?",
        (project_id,),
    ).fetchall()
    return (
        {r["id"]: TaskRevision.model_validate_json(r["data"]) for r in rows},
        {r["id"]: r["position"] for r in rows},
    )


class ContextReads:
    def __init__(self, workspace: Workspace, browser_origin: str = ""):
        self.workspace = workspace
        self.browser_origin = browser_origin.rstrip("/")

    def url(self, project_id: str, *segments: str) -> str:
        path = "/".join(quote(s, safe="") for s in ("projects", project_id, *segments))
        return f"{self.browser_origin}/{path}"

    def _summary(
        self,
        item: TaskRevision,
        completed: set[str],
        position: int,
        db: sqlite3.Connection,
        records: dict[str, TaskRevision],
    ) -> dict[str, Any]:
        blocked = [p for p in item.dependencies if p not in completed]
        questions = blocking_questions(db, item.project_id, item.id)
        publication = publication_status(item)
        return excerpt(
            {
                "id": item.id,
                "key": item.key,
                "url": self.url(item.project_id, "tasks", item.key),
                "title": item.title,
                "task_type": item.task_type,
                "status": item.status,
                "milestone_id": item.milestone_id,
                "archived": item.archived,
                "revision": item.revision,
                "position": position,
                "readiness": readiness(
                    item,
                    publication,
                    blocked=bool(blocked or questions),
                    stages_stale=stages_need_reconciliation(
                        db, item.project_id, item.id, item.agreement_revision
                    ),
                ),
                "publication_status": publication,
                "preparation_issue": preparation_issue(db, item),
                "agreement_revision": item.agreement_revision,
                "prerequisite_count": len(item.dependencies),
                "blocked_count": len(blocked),
                "blocked_by_keys": [records[key].key for key in blocked[:3]],
                "blocking_question_count": len(questions),
                "reconciliation_reason": item.reconciliation_reason,
            },
            500,
        )

    def projects(self, *, after: str | None = None, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        validate_page(limit)
        with self.workspace.connection() as db:
            rows = db.execute(
                "SELECT id, name, path, task_prefix, revision FROM projects "
                "WHERE id>? ORDER BY id LIMIT ?",
                (after or "", limit + 1),
            ).fetchall()
            return page(
                [excerpt({**dict(r), "url": self.url(r["id"])}, 500) for r in rows], limit, "id"
            )

    def project(self, project_id: str) -> dict[str, Any]:
        return {
            **excerpt(self.workspace.project(project_id).model_dump()),
            "url": self.url(project_id),
        }

    def coordinator_history(
        self, project_id: str, *, before: int | None = None, limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        """Bounded public exchanges, not native history or portable permission grants."""
        validate_page(limit, before)
        if before is not None and before > 2**63 - 1:
            raise ApplicationError("invalid_request", "Cursor exceeds the saved history range.")
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            rows = coordinator_exchanges(db, project_id, before or 2**63 - 1, limit + 1)
            items = []
            for row in rows:
                revision = row["revision"]
                item = excerpt(
                    {key: value for key, value in row.items() if key != "task_context"},
                    1000,
                )
                item["text_sources"] = {
                    field: {
                        "tool": "get_text",
                        "resource": "coordinator",
                        "identity": row["id"],
                        "field": field,
                        "revision": revision,
                        "url": f"{self.browser_origin}/api/context/projects/"
                        f"{quote(project_id, safe='')}/text?"
                        + urlencode(
                            {
                                "resource": "coordinator",
                                "identity": row["id"],
                                "field": field,
                                "revision": revision,
                            }
                        ),
                    }
                    for field in ("human", "coordinator")
                }
                items.append(item)
            return page(items, limit, "number")

    def milestones(
        self, project_id: str, *, after: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        validate_page(limit)
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            rows = db.execute(
                "SELECT json_remove(data, '$.body') FROM milestones WHERE project_id=? "
                "AND id>? ORDER BY id LIMIT ?",
                (project_id, after or "", limit + 1),
            ).fetchall()
            return page([excerpt(json.loads(r[0]), 500) for r in rows], limit, "id")

    def milestone(self, project_id: str, milestone_id: str) -> dict[str, Any]:
        return excerpt(self.workspace.milestone(project_id, milestone_id).model_dump())

    def tasks(
        self,
        project_id: str,
        *,
        include_archived: bool = False,
        status: str | None = None,
        task_type: str | None = None,
        milestone_id: str | None = None,
        readiness: str | None = None,
        query: str | None = None,
        after: int | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        validate_page(limit, after)
        if status is not None and status not in STATUSES:
            raise ApplicationError("invalid_request", "Unknown work status.")
        if readiness is not None and readiness not in (
            "ready",
            "blocked",
            "draft",
            "needs_reconciliation",
        ):
            raise ApplicationError("invalid_request", "Unknown readiness.")
        if task_type is not None and task_type not in (
            "bug",
            "feature",
            "maintenance",
            "investigation",
        ):
            raise ApplicationError("invalid_request", "Unknown task type.")
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            records, positions = task_metadata(db, project_id)
            if milestone_id:
                milestone_id = self.workspace._milestone(db, project_id, milestone_id).id
            completed = self.workspace._completed(records, db)
            items = []
            # Immutable task number gives stable pages even while priorities/status change.
            for item in sorted(records.values(), key=lambda t: int(t.key.split("-")[1])):
                number = int(item.key.split("-")[1])
                if after is not None and number <= after:
                    continue
                summary = self._summary(item, completed, positions[item.id], db, records)
                if (
                    (item.archived and not include_archived)
                    or (status is not None and item.status != status)
                    or (task_type is not None and item.task_type != task_type)
                    or (milestone_id is not None and (item.milestone_id or "") != milestone_id)
                    or (readiness is not None and summary["readiness"] != readiness)
                    or (query and query.casefold() not in (item.key + " " + item.title).casefold())
                ):
                    continue
                items.append({**summary, "number": number})
                if len(items) > limit:
                    break
            return page(items, limit, "number")

    def overview(self, project_id: str) -> dict[str, Any]:
        with self.workspace.connection() as db:
            project = {
                **excerpt(self.workspace._project(db, project_id).model_dump()),
                "url": self.url(project_id),
            }
            records, positions = task_metadata(db, project_id)
            completed = self.workspace._completed(records, db)
            summaries = [
                self._summary(t, completed, positions[t.id], db, records)
                for t in records.values()
                if not t.archived
            ]
            entered = dict(
                db.execute(
                    "WITH changes AS (SELECT task_id, json_extract(data, '$.status') AS status, "
                    "json_extract(data, '$.updated_at') AS updated_at, "
                    "lag(json_extract(data, '$.status')) OVER "
                    "(PARTITION BY task_id ORDER BY revision) "
                    "AS previous FROM task_revisions WHERE project_id=?) "
                    "SELECT task_id, max(updated_at) FROM changes WHERE status IS NOT previous "
                    "GROUP BY task_id",
                    (project_id,),
                ).fetchall()
            )
            columns: list[dict[str, Any]] = []
            for status in STATUSES:
                items = sorted(
                    (t for t in summaries if t["status"] == status),
                    key=lambda t: (
                        t["position"] if status in ("backlog", "up_next") else entered[t["id"]],
                        t["key"],
                    ),
                    reverse=status == "done",
                )
                columns.append(
                    {
                        "status": status,
                        "count": len(items),
                        "tasks": items[:3],
                        "has_more": len(items) > 3,
                    }
                )
            result = {
                "project": project,
                "columns": columns,
                **Questions(self.workspace).counts(db, project_id),
                "archived_count": sum(t.archived for t in records.values()),
                "blocked_count": sum(t["readiness"] == "blocked" for t in summaries),
                "reconciliation_count": sum(
                    t["readiness"] == "needs_reconciliation" for t in summaries
                ),
                **self._briefing(db, project_id, summaries, positions),
            }
            while size(result) > PAGE_BYTES - 200:
                largest = max(columns, key=lambda c: size(c["tasks"]))
                largest["tasks"].pop()
                largest["has_more"] = True
            for column in columns:
                column["omitted_count"] = column["count"] - len(column["tasks"])
            return result

    def _worker_briefing(self, db: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        from flowfield.execution_models import WorkerSettings

        row = db.execute(
            "SELECT data FROM worker_settings WHERE project_id=?", (project_id,)
        ).fetchone()
        settings = json.loads(row[0]) if row else WorkerSettings(project_id=project_id).model_dump()
        rows = db.execute(
            "SELECT data FROM runs r WHERE project_id=? AND "
            "status IN ('preparing','running','stopping','uncertain','failed','in_review') "
            "AND NOT EXISTS (SELECT 1 FROM runs n WHERE n.project_id=r.project_id "
            "AND n.task_id=r.task_id AND n.number>r.number) ORDER BY number DESC LIMIT 4",
            (project_id,),
        ).fetchall()
        runs = []
        for row in rows[:3]:
            run = json.loads(row[0])
            runs.append(
                {
                    **{key: run[key] for key in ("id", "task_key", "status", "revision")},
                    "url": self.url(project_id, "tasks", run["task_key"], "runs", run["id"]),
                }
            )
        occupancy = dict(
            db.execute(
                "SELECT status,count(*) FROM runs WHERE project_id=? "
                "AND status IN ('preparing','running','stopping','uncertain') GROUP BY status",
                (project_id,),
            ).fetchall()
        )
        return {
            **excerpt(settings, 600),
            "attempts": runs,
            "has_more": len(rows) > 3,
            "occupied_slots": sum(occupancy.values()),
            "uncertain_slots": occupancy.get("uncertain", 0),
        }

    def _briefing(
        self,
        db: sqlite3.Connection,
        project_id: str,
        summaries: list[dict[str, Any]],
        positions: dict[str, int],
    ) -> dict[str, Any]:
        """Derived orientation, never a second mutable summary or a launch instruction."""
        results = result_brief(self.workspace, db, project_id)
        for item in results["items"]:
            item["url"] = self.url(project_id, "tasks", item["task_key"], "result", item["id"])
        attention = {}
        for status in ("open", "answered"):
            rows = db.execute(
                "SELECT id, task_id, json_extract(data, '$.question') AS question, "
                "json_extract(data, '$.origin_run_id') AS origin_run_id, "
                "json_extract(data, '$.revision') AS revision FROM questions "
                "WHERE project_id=? AND status=? ORDER BY number LIMIT 3",
                (project_id, status),
            ).fetchall()
            count = db.execute(
                "SELECT count(*) FROM questions WHERE project_id=? AND status=?",
                (project_id, status),
            ).fetchone()[0]
            attention[status] = {
                "items": [
                    {
                        **excerpt(dict(r), 400),
                        "url": self.url(project_id, "inbox", r["id"]),
                        "delivery": delivery.model_dump()
                        if (delivery := question_delivery(db, project_id, r["id"]))
                        else None,
                    }
                    for r in rows
                ],
                "count": count,
                "omitted_count": max(0, count - len(rows)),
            }
        window_start = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        # Project questions also write task-local events; show the canonical event once.
        change_filter = (
            " FROM activity a WHERE a.project_id=? AND a.created_at>=? "
            "AND NOT (a.task_id IS NOT NULL AND a.question_id IS NOT NULL AND EXISTS "
            "(SELECT 1 FROM activity p WHERE p.project_id=a.project_id AND p.task_id IS NULL "
            "AND p.question_id=a.question_id AND p.created_at=a.created_at))"
        )
        changes = db.execute(
            "SELECT id, task_id, question_id, kind, body, task_revision, sequence, created_at"
            + change_filter
            + " ORDER BY sequence DESC LIMIT 5",
            (project_id, window_start),
        ).fetchall()
        change_count = db.execute(
            "SELECT count(*)" + change_filter,
            (project_id, window_start),
        ).fetchone()[0]
        ready = sorted(
            (t for t in summaries if t["status"] == "up_next" and t["readiness"] == "ready"),
            key=lambda t: (positions[t["id"]], t["key"]),
        )
        recommendation: dict[str, Any] = {
            "action": "plan",
            "reason": "No ready Up next task; review blockers or choose work with the human.",
        }
        for action, candidates, reason in (
            (
                "reconcile",
                [t for t in summaries if t["readiness"] == "needs_reconciliation"],
                "Changed requirements or prerequisites need reconciliation.",
            ),
            (
                "apply_answer",
                [q for q in attention["answered"]["items"] if not q["origin_run_id"]],
                "A saved answer awaits explicit application.",
            ),
            (
                "inspect_result",
                [r for r in results["items"] if r["owner"] in ("human", "coordinator")],
                "Follow the result's owner, blocker and next_action; approval binds its version.",
            ),
            (
                "resume",
                [t for t in summaries if t["status"] == "in_progress"],
                "Inspect the current attempt before intervening; "
                "the service owns managed execution.",
            ),
            (
                "publish",
                sorted(
                    (
                        t
                        for t in summaries
                        if t["status"] == "up_next"
                        and t["publication_status"] != "published"
                        and not t["blocking_question_count"]
                    ),
                    key=lambda t: positions[t["id"]],
                ),
                "Clarify the draft, apply answers and publish a coherent assignment.",
            ),
            (
                "work",
                ready,
                "First ready task in Up next order; check worker settings and queue state.",
            ),
        ):
            if candidates:
                recommendation = {
                    "action": action,
                    "id": candidates[0]["id"],
                    "reason": reason,
                    "url": candidates[0]["url"],
                }
                if "key" in candidates[0]:
                    recommendation["task_key"] = candidates[0]["key"]
                if "next_action" in candidates[0]:
                    recommendation["action"] = candidates[0]["next_action"]
                    recommendation["owner"] = candidates[0]["owner"]
                break
        root = Path(self.workspace._project(db, project_id).path)
        guidance = [
            name
            for name in (
                "AGENTS.md",
                "AGENTS.override.md",
                ".agents/skills/flowfield-coordinator/SKILL.md",
                "README.md",
                "CONTRIBUTING.md",
            )
            if (root / name).is_file()
        ]
        return {
            "observed_at": now(),
            "execution": "managed_local",
            "workers": self._worker_briefing(db, project_id),
            "results": results,
            "links": {
                "board": self.url(project_id),
                "inbox": self.url(project_id, "inbox"),
            },
            "attention": attention,
            "recent_changes": {
                "window_start": window_start,
                "items": [excerpt(dict(r), 500) for r in changes],
                "count": change_count,
                "omitted_count": change_count - len(changes),
            },
            "recommendation": recommendation,
            "repository_guidance": {
                "paths": guidance,
                "scope": "Root entry points only; also read applicable nested instructions.",
            },
            "read_more": (
                "get_task for description, blockers and handoff; get_question for answers; "
                "list_tasks/list_questions for omitted items; list_activity for history; "
                "get_text for excerpts."
                " search_context finds current or historical evidence with source references. "
                "get_results/get_result/get_executions inspect omitted evidence."
            ),
        }

    def _handoff(
        self, db: sqlite3.Connection, project_id: str, task: TaskRevision
    ) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT id, body, author, created_at, task_revision FROM activity "
            "WHERE project_id=? AND task_id=? AND kind='handoff' ORDER BY sequence DESC LIMIT 1",
            (project_id, task.id),
        ).fetchone()
        if row is None:
            return None
        return {
            **excerpt(dict(row), 1500),
            "needs_recheck": row["task_revision"] != task.revision,
            "freshness_scope": (
                "Task revision only; verify repository state and current questions "
                "before continuing."
            ),
        }

    def task(self, project_id: str, task_id: str) -> dict[str, Any]:
        with self.workspace.connection() as db:
            identity = self.workspace._activity_scope(db, project_id, task_id)
            row = db.execute(
                "SELECT data FROM tasks WHERE project_id=? AND id=?", (project_id, identity)
            ).fetchone()
            current = json.loads(row[0])
            assert identity is not None
            records, positions = task_metadata(db, project_id)
            completed = self.workspace._completed(records, db)
            item = records[identity]
            summary = self._summary(item, completed, positions[item.id], db, records)
            summary.pop("truncated_fields", None)
            result = {
                **summary,
                **excerpt({k: v for k, v in current.items() if k != "dependencies"}),
            }
            for name, ids in {
                "prerequisites": item.dependencies,
                "blocked_by": [p for p in item.dependencies if p not in completed],
                "dependents": [t.id for t in records.values() if item.id in t.dependencies],
            }.items():
                result[name] = [
                    self._summary(records[p], completed, positions[p], db, records) for p in ids[:5]
                ]
                result[name + "_count"] = len(ids)
            result["blocking_questions"] = [
                {**excerpt(q.model_dump(), 300), "url": self.url(project_id, "inbox", q.id)}
                for q in blocking_questions(db, project_id, identity)[:5]
            ]
            result["dependencies"] = item.dependencies[:5]
            latest = self.workspace._latest_update(db, project_id, item.id)
            result["latest_update"] = excerpt(latest.model_dump(), 1000) if latest else None
            result["handoff"] = self._handoff(db, project_id, item)
            proposed = result_brief(self.workspace, db, project_id, item.id, 1)["items"]
            if proposed:
                result["proposed_result"] = proposed[0]
                result["proposed_result"]["url"] = self.url(
                    project_id, "tasks", item.key, "result", proposed[0]["id"]
                )
            latest_run = db.execute(
                "SELECT data FROM runs WHERE project_id=? AND task_id=? "
                "ORDER BY number DESC LIMIT 1",
                (project_id, item.id),
            ).fetchone()
            if latest_run:
                run = json.loads(latest_run[0])
                result["execution"] = {
                    key: run[key]
                    for key in (
                        "id",
                        "revision",
                        "status",
                        "model",
                        "effort",
                        "result_commit",
                        "code_available",
                    )
                }
                result["execution"]["url"] = (
                    self.url(project_id, "tasks", item.key) + "/runs/" + run["id"]
                )
            if latest and latest.kind == "handoff":
                # The selected checkpoint has its own field; do not repeat its narrative.
                result["latest_update"] = {
                    "id": latest.id,
                    "kind": latest.kind,
                    "body_in": "handoff",
                }
            while size(result) > PAGE_BYTES - 200:
                largest = max(
                    ("prerequisites", "blocked_by", "dependents"), key=lambda k: size(result[k])
                )
                result[largest].pop()
            result["dependencies"] = [p["id"] for p in result["prerequisites"]]
            result["dependencies_truncated"] = len(result["dependencies"]) < len(item.dependencies)
            return result

    def relationships(
        self,
        project_id: str,
        task_id: str,
        *,
        relation: Literal["prerequisites", "blocked_by", "dependents"] = "prerequisites",
        after: int | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        validate_page(limit, after)
        with self.workspace.connection() as db:
            identity = self.workspace._activity_scope(db, project_id, task_id)
            assert identity is not None
            records, positions = task_metadata(db, project_id)
            completed = self.workspace._completed(records, db)
            item = records[identity]
            ids = (
                item.dependencies
                if relation == "prerequisites"
                else [p for p in item.dependencies if p not in completed]
                if relation == "blocked_by"
                else [t.id for t in records.values() if item.id in t.dependencies]
            )
            items = []
            for key in sorted(ids, key=lambda p: int(records[p].key.split("-")[1])):
                number = int(records[key].key.split("-")[1])
                if after is None or number > after:
                    items.append(
                        {
                            **self._summary(records[key], completed, positions[key], db, records),
                            "number": number,
                        }
                    )
                if len(items) > limit:
                    break
            return page(items, limit, "number")

    def revisions(
        self,
        project_id: str,
        task_id: str,
        *,
        before: int | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        validate_page(limit, before)
        with self.workspace.connection() as db:
            identity = self.workspace._activity_scope(db, project_id, task_id)
            rows = db.execute(
                "SELECT json_remove(data, '$.body', '$.dependencies') "
                "FROM task_revisions WHERE project_id=? AND task_id=? AND revision<? "
                "ORDER BY revision DESC LIMIT ?",
                (project_id, identity, before or 2**63 - 1, limit + 1),
            ).fetchall()
            return page([excerpt(json.loads(r[0]), 500) for r in rows], limit, "revision")

    def activity(
        self,
        project_id: str,
        *,
        task_id: str | None = None,
        kind: EntryKind | None = None,
        current_only: bool = False,
        before: int | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        validate_page(limit, before)
        source = self.workspace.activity(
            project_id,
            task_id=task_id,
            kind=kind,
            current_only=current_only,
            before=before,
            limit=limit,
        )
        result = page([excerpt(e.model_dump(), 1000) for e in source.items], limit, "sequence")
        if result["next_cursor"] is None:
            result["next_cursor"] = source.next_cursor
        return result

    def activity_entry(self, project_id: str, entry_id: str) -> dict[str, Any]:
        return excerpt(self.workspace.activity_entry(project_id, entry_id).model_dump(), 2000)

    def questions(
        self,
        project_id: str,
        *,
        status: str = "active",
        task_id: str | None = None,
        after: int | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        source = Questions(self.workspace).list(
            project_id, status=status, task_id=task_id, after=after, limit=limit
        )
        result = page(
            [
                {**item, "url": self.url(project_id, "inbox", item["id"])}
                for item in source["items"]
            ],
            limit,
            "number",
            budget=PAGE_BYTES - 200,
        )
        result["next_cursor"] = result["next_cursor"] or source["next_cursor"]
        return {**source, **result}

    def question(
        self,
        project_id: str,
        identity: str,
        revision: int | None = None,
    ) -> dict[str, Any]:
        return {
            **excerpt(Questions(self.workspace).get(project_id, identity, revision).model_dump()),
            "url": self.url(project_id, "inbox", identity),
        }

    def result(self, project_id: str, identity: str) -> dict[str, Any]:
        from flowfield.results import Results

        value = Results(self.workspace).get(project_id, identity).model_dump()
        value["report"] = excerpt(value["report"], 2000)
        if value["correction"]:
            value["correction"] = excerpt(value["correction"], 1500)
        return excerpt(value, 2000)

    def text(
        self,
        project_id: str,
        resource: Literal[
            "task", "milestone", "project", "activity", "question", "result", "coordinator"
        ],
        identity: str | None = None,
        field: str = "body",
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 4000:
            raise ApplicationError(
                "invalid_request", "Offset must be nonnegative; limit must be 1–4000 characters."
            )
        allowed = {
            "coordinator": ("human", "coordinator"),
            "task": (
                "body",
                "change_note",
                "reconciliation_reason",
                "dependencies",
            ),
            "project": ("description", "path"),
            "milestone": ("body",),
            "activity": ("body",),
            "result": ("summary", "checks", "limitations", "feedback", "problem", "correction"),
            "question": (
                "question",
                "context",
                "recommendation",
                "blocking_scope",
                "answer",
                "decision",
            ),
        }
        if field not in allowed[resource] or (resource != "project" and identity is None):
            raise ApplicationError(
                "invalid_request", "Choose an identity and a text field for this resource."
            )
        item: dict[str, Any]
        if resource == "coordinator":
            from flowfield.coordinator_store import CoordinatorStore

            turn = CoordinatorStore(self.workspace).get(project_id, identity or "")
            if revision is not None and turn.activity.revision != revision:
                raise ApplicationError(
                    "revision_conflict", "Coordinator output changed. Start reading again.", 409
                )
            item = {
                "id": turn.id,
                "revision": turn.activity.revision,
                "human": turn.text,
                "coordinator": "\n\n".join(
                    entry.text for entry in turn.activity.items if entry.kind == "agent"
                ),
                "output_omitted": coordinator_output_omitted(turn.activity.model_dump()),
            }
        elif resource == "task":
            with self.workspace.connection() as db:
                task_id = self.workspace._activity_scope(db, project_id, identity)
                row = db.execute(
                    "SELECT data FROM task_revisions WHERE project_id=? AND task_id=? "
                    "AND (? IS NULL OR revision=?) ORDER BY revision DESC LIMIT 1",
                    (project_id, task_id, revision, revision),
                ).fetchone()
                if row is None:
                    raise ApplicationError("not_found", "Task revision not found.", 404)
                item = json.loads(row[0])
        elif resource == "question":
            item = Questions(self.workspace).get(project_id, identity or "", revision).model_dump()
        elif resource == "activity":
            item = self.workspace.activity_entry(project_id, identity or "").model_dump()
        elif resource == "result":
            from flowfield.results import Results

            result = Results(self.workspace).get(project_id, identity or "")
            if revision is not None and result.revision != revision:
                raise ApplicationError(
                    "revision_conflict", "Result changed; reread its reference.", 409
                )
            item = {
                **result.model_dump(),
                **result.report.model_dump(),
                "correction": result.correction.details if result.correction else "",
            }
        else:
            item = (
                self.workspace.project(project_id)
                if resource == "project"
                else self.workspace.milestone(project_id, identity or "")
            ).model_dump()
            if revision is not None and item["revision"] != revision:
                raise ApplicationError(
                    "revision_conflict", "Text changed. Start reading again.", 409
                )
        body = json.dumps(item[field]) if field == "dependencies" else item[field] or ""
        text = clip(body[offset : offset + limit], PAGE_BYTES - 1000)
        return {
            "id": item["id"],
            "revision": item.get("revision"),
            "field": field,
            "text": text,
            "offset": offset,
            "total_characters": len(body),
            "next_offset": offset + len(text) if offset + len(text) < len(body) else None,
            **({"output_omitted": item["output_omitted"]} if resource == "coordinator" else {}),
        }
