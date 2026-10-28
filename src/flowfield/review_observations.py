"""Read retained historical result notes; new feedback uses exact result decisions."""

import json
import sqlite3

from flowfield.errors import ApplicationError


def source(db: sqlite3.Connection, workspace: str, result: str, section: str) -> str:
    if section.startswith("observation:"):
        identity = section.removeprefix("observation:")
        row = db.execute(
            "SELECT data FROM task_replies WHERE workspace_id=? AND id=? "
            "AND json_extract(data,'$.binding.result_id')=? "
            "AND json_extract(data,'$.action')='observation'",
            (workspace, identity, result),
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Observation not found for this result.", 404)
        return str(row[0])
    try:
        offset = int(section.split(":")[1]) if ":" in section else 0
        if offset < 0:
            raise ValueError
    except ValueError:
        raise ApplicationError("invalid_cursor", "Use the returned observations section.") from None
    page = db.execute(
        "SELECT id,data FROM task_replies WHERE workspace_id=? "
        "AND json_extract(data,'$.binding.result_id')=? "
        "AND json_extract(data,'$.action')='observation' ORDER BY created_at,id LIMIT 21 OFFSET ?",
        (workspace, result, offset),
    ).fetchall()
    return json.dumps(
        {
            "authority": "Observations about this exact result. Assess them as evidence; "
            "scope changes require a human decision.",
            "items": [
                {
                    "section": "observation:" + row["id"],
                    "summary": json.loads(row["data"])["body"][:200],
                }
                for row in page[:20]
            ],
            "next_section": f"observations:{offset + 20}" if len(page) > 20 else None,
        }
    )
