"""Bounded navigation of exact source text; offsets are Unicode characters, not bytes."""

import hashlib
import re
from typing import Any, Literal

from flowfield.errors import ApplicationError

Mode = Literal["read", "outline", "search"]


def navigate(
    text: str,
    *,
    mode: Mode = "read",
    offset: int = 0,
    limit: int = 6000,
    query: str | None = None,
    end: int | None = None,
) -> dict[str, Any]:
    if offset < 0 or offset > len(text) or not 1 <= limit <= 6000:
        raise ApplicationError(
            "invalid_offset", "Choose a source offset and a 1–6000 character limit."
        )
    if end is not None and not offset <= end <= len(text):
        raise ApplicationError("invalid_range", "End must lie between offset and source length.")
    identity = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "total_chars": len(text)}
    if mode == "read":
        stop = min(len(text) if end is None else end, offset + limit)
        return {
            **identity,
            "text": text[offset:stop],
            "offset": offset,
            "end_offset": stop,
            "start_line": text.count("\n", 0, offset) + 1,
            "next_offset": stop if stop < (len(text) if end is None else end) else None,
        }
    if mode == "outline":
        # ATX headings outside fenced code; offsets let callers read the complete section.
        headings: list[dict[str, Any]] = []
        position = 0
        fence = ""
        for line_no, line in enumerate(text.splitlines(keepends=True), 1):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if marker:
                token = marker[1]
                if not fence:
                    fence = token
                elif (
                    token[0] == fence[0]
                    and len(token) >= len(fence)
                    and not line[marker.end() :].strip()
                ):
                    fence = ""
            elif not fence and (match := re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", line)):
                headings.append(
                    {
                        "title": re.sub(r"[ \t]+#+[ \t]*$", "", match[2])[:200],
                        "level": len(match[1]),
                        "offset": position,
                        "line": line_no,
                    }
                )
            position += len(line)
        following: list[dict[str, Any]] = []
        for heading in reversed(headings):
            while following and following[-1]["level"] > heading["level"]:
                following.pop()
            heading["end_offset"] = following[-1]["offset"] if following else len(text)
            following.append(heading)
        selected = [item for item in headings if item["offset"] >= offset]
        return {
            **identity,
            "headings": selected[:20],
            "next_offset": selected[20]["offset"] if len(selected) > 20 else None,
            "format": "atx-headings",
        }
    if mode != "search" or not query or not query.strip() or len(query) > 500:
        raise ApplicationError(
            "invalid_query", "Supply a literal search query of 1–500 characters."
        )
    matches = []
    for match in re.finditer(re.escape(query), text[offset:], re.IGNORECASE):
        start, stop = offset + match.start(), offset + match.end()
        left, right = max(0, start - 100), min(len(text), stop + 100)
        matches.append(
            {
                "offset": start,
                "end_offset": stop,
                "line": text.count("\n", 0, start) + 1,
                "excerpt": text[left:right],
                "excerpt_offset": left,
            }
        )
        if len(matches) == 11:
            break
    return {
        **identity,
        "matches": matches[:10],
        "next_offset": matches[10]["offset"] if len(matches) > 10 else None,
    }
