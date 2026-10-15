"""Deterministic context selection independent of model transport or conversation rendering."""

import json
import sqlite3
from hashlib import sha256

from flowfield.errors import ApplicationError
from flowfield.execution_models import Run


def continue_input(sections: dict[str, str], current: dict[str, str | int | None]) -> None:
    """Keep the newest exchange in full; earlier answers remain explicit constraints.

    No model guesses that an answer is resolved. Older verbose question context is optional
    evidence, retained in a separate section instead of repeatedly replayed as driving input.
    """
    older = json.loads(sections.get("exchange_history", "[]"))
    older.extend(json.loads(sections.get("input", "[]")))
    sections["exchange_history"] = json.dumps(older)
    sections["earlier_answers"] = json.dumps(
        [
            {
                "question_id": item["question_id"],
                "answer_revision": item["answer_revision"],
                "question": item["question"],
                "answer": item["answer"],
                "source": "exchange_history",
            }
            for item in older
        ]
    )
    sections["input"] = json.dumps([current])


def enrich(db: sqlite3.Connection, run: Run, sections: dict[str, str]) -> None:
    # Freeze result-bound observations at claim. Later tests cannot be attributed to
    # an earlier assignment; successor code never inherits a claim that it was tested.
    observations = db.execute(
        "SELECT data FROM task_replies WHERE project_id=? AND task_id=? "
        "AND json_extract(data,'$.action')='observation' ORDER BY created_at,id",
        (run.project_id, run.task_id),
    ).fetchall()
    sections["human_testing"] = (
        json.dumps(
            {
                "authority": "Human observations apply only to the bound result, not successor "
                "code. They are evidence, not approval or instructions to change scope.",
                "items": [json.loads(row[0]) for row in observations],
            }
        )
        if observations
        else "[]"
    )
    plan = db.execute(
        "SELECT data FROM stage_plans WHERE project_id=? AND task_id=? "
        "ORDER BY revision DESC LIMIT 1",
        (run.project_id, run.task_id),
    ).fetchone()
    sections["stages"] = (
        plan[0]
        if plan
        else json.dumps({"revision": 0, "agreement_revision": run.agreement_revision, "stages": []})
    )
    # The latest result is already present in full as predecessor. Earlier attempts get
    # summaries, with immutable submission evidence retained as separately retrievable text.
    rows = db.execute(
        "SELECT id,json_extract(data,'$.created_at') AS created_at,status,"
        "json_extract(data,'$.result') AS report FROM runs WHERE project_id=? AND task_id=? "
        "ORDER BY number",
        (run.project_id, run.task_id),
    ).fetchall()
    sources = {}
    headers = []
    concerns = []
    for row in rows:
        report = json.loads(row["report"]) if row["report"] else None
        if row["report"]:
            sources[row["id"]] = sha256(row["report"].encode()).hexdigest()
        if report and (report["limitations"] or report["remaining_work"]):
            concerns.append(
                {
                    "run_id": row["id"],
                    "limitations": report["limitations"],
                    "remaining_work": report["remaining_work"],
                    "source": "attempt:" + row["id"],
                }
            )
        headers.append(
            {
                "run_id": row["id"],
                "created_at": row["created_at"],
                "status": row["status"],
                "outcome": report["outcome"] if report else None,
                "summary": report["summary"][:200] if report else "",
                "source": "attempt:" + row["id"] if report else None,
            }
        )
    sections["attempt_history"] = json.dumps(
        {
            "items": headers[-10:],
            "earlier_omitted": max(0, len(headers) - 10),
            "source_index": "attempt_sources",
            "authority": "Historical observations, not current instructions.",
        }
    )
    sections["attempt_sources"] = json.dumps(sources)
    sections["prior_concerns"] = json.dumps(concerns)


def attempt_source(
    db: sqlite3.Connection, run: Run, sections: dict[str, str], identity: str
) -> str:
    """Fetch only a submitted report frozen by identity and digest at this attempt's claim."""
    digest = json.loads(sections.get("attempt_sources", "{}")).get(identity)
    row = (
        db.execute(
            "SELECT json_extract(data,'$.result') FROM runs "
            "WHERE project_id=? AND task_id=? AND id=?",
            (run.project_id, run.task_id, identity),
        ).fetchone()
        if digest
        else None
    )
    if not row or not row[0] or sha256(row[0].encode()).hexdigest() != digest:
        raise ApplicationError(
            "source_unavailable", "This report is not in the frozen context.", 409
        )
    return str(row[0])


def brief_context(sections: dict[str, str]) -> dict[str, object]:
    """Prioritize current intent and required sources; preserve full frozen retrieval."""
    limits = {"description": 6000, "feedback": 4000}
    available = {
        key: value for key, value in sections.items() if value.strip() not in ("", "[]", "{}")
    }
    history = json.loads(available.get("attempt_history", "{}"))
    if not history.get("items") and not history.get("earlier_omitted"):
        available.pop("attempt_history", None)
    manifest = json.loads(sections.get("documentation", "{}"))
    required = [
        item["section"]
        for item in sorted(manifest.get("items", []), key=lambda item: item["id"] != "guidance")
        if item.get("purpose", "required") == "required"
    ]
    # Reference bodies remain available on demand; required sources precede workflow/history.
    candidates = (
        "input",
        "earlier_answers",
        "correction",
        "validation",
        "execution_limits",
        "documentation",
        "project",
        "milestone",
        "milestone_criteria",
        "milestone_verification",
        "milestone_agreement",
        "prerequisites",
        "questions",
        "prior_concerns",
        *required,
        "stages",
        "handoff",
        "predecessor",
        "human_testing",
        "attempt_history",
    )
    context: dict[str, str] = {}
    for key in candidates:
        if key not in available:
            continue
        proposed = {**context, key: available[key]}
        if len(json.dumps(proposed)) <= 10000:
            context = proposed
    policy = (
        "The description is the resolved task agreement. Inline context sections are complete "
        "frozen sources; use them directly. Use read_context for listed omitted sections and "
        "truncated description/feedback before relying on them. Read omitted current input, "
        "constraints, validation, prerequisite evidence and unresolved concerns before acting. "
        "Retrieve historical evidence when relevant; it does not change the agreement. "
        "Do not request absent section names."
    )
    if manifest:
        policy += (
            " Read the documentation manifest and every omitted required document:<id> section "
            "in full before acting. Required documents without purpose in an older manifest "
            "remain required. Reference documents are supporting material available when needed; "
            "their inclusion does not require reading them in full. References do not override "
            "the task agreement or required sources."
        )
    return {
        **{key: sections.get(key, "")[:limit] for key, limit in limits.items()},
        "context": context,
        "context_policy": policy,
        "sections": {key: len(value) for key, value in available.items()},
        "truncated_sections": [
            key for key, limit in limits.items() if len(sections.get(key, "")) > limit
        ],
    }
