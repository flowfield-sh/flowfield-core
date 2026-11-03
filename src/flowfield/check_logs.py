"""Serve exact retained command bytes through their owning receipt."""

import hashlib
import re
from pathlib import Path
from typing import Any

from fastapi.responses import Response

from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult
from flowfield.source_navigation import navigate


def verified_bytes(runtime: Path, checks: list[CheckResult], identity: str) -> bytes:
    """Read only bytes matching a current receipt; never reopen after verification."""
    check = next((item for item in checks if item.log_id == identity), None)
    if check is None or not re.fullmatch("[a-f0-9]{32}", identity):
        raise ApplicationError("not_found", "No retained log in this attempt's checks.", 404)
    path = runtime / "logs" / identity
    try:
        if any(parent.is_symlink() for parent in [path, *path.parents]):
            raise ValueError("Linked log")
        # The producer retains at most 8 MiB. Bound reads even if the file was replaced.
        with path.open("rb") as stream:
            data = stream.read(8 * 1024 * 1024 + 1)
        if (
            len(data) > 8 * 1024 * 1024
            or len(data) != check.log_size
            or hashlib.sha256(data).hexdigest() != check.log_sha256
        ):
            raise ValueError("Changed log")
    except (OSError, ValueError) as error:
        raise ApplicationError(
            "log_unavailable", "The retained command log is missing or changed.", 409
        ) from error
    return data


def read(runtime: Path, checks: list[CheckResult], identity: str) -> str:
    return verified_bytes(runtime, checks, identity).decode("utf-8", errors="replace")


def page(
    receipts: tuple[Path, list[CheckResult]], index: int, offset: int, limit: int
) -> dict[str, Any]:
    runtime, checks = receipts
    if index < 0 or index >= len(checks):
        raise ApplicationError("not_found", "Command receipt not found.", 404)
    check = checks[index]
    if not check.log_id:
        raise ApplicationError("not_found", "No retained log for this command.", 404)
    data = verified_bytes(runtime, checks, check.log_id)
    try:
        text = data.decode("utf-8")
        digest = check.log_sha256
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        digest = None  # Navigation identifies decoded text; receipt identifies original bytes.
    result = navigate(text, offset=offset, limit=limit, sha256=digest)
    if result["next_request"]:
        result["next_request"] = {"offset": result["next_offset"], "limit": limit}
    return result


def sections(checks: list[CheckResult]) -> list[dict[str, object]]:
    """Receipts advertise a scoped text source without exposing filesystem paths."""
    return [
        {
            **check.model_dump(),
            "log_section": f"project_check_log:{check.log_id}" if check.log_id else None,
        }
        for check in checks
    ]


def download(runtime: Path, checks: list[CheckResult], index: int) -> Response:
    if index < 0 or index >= len(checks):
        raise ApplicationError("not_found", "Command receipt not found.", 404)
    identity = checks[index].log_id
    if not identity:
        raise ApplicationError("not_found", "No retained log for this command.", 404)
    return Response(
        verified_bytes(runtime, checks, identity),
        media_type="text/plain",
        headers={
            "Content-Disposition": 'attachment; filename="command.log"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
