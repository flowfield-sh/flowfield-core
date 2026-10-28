"""Serve exact retained command bytes through their owning receipt."""

import hashlib
import re
from pathlib import Path

from fastapi.responses import FileResponse

from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult


def download(runtime: Path, checks: list[CheckResult], index: int) -> FileResponse:
    if index < 0 or index >= len(checks):
        raise ApplicationError("not_found", "Command receipt not found.", 404)
    check = checks[index]
    if not check.log_id or not re.fullmatch("[a-f0-9]{32}", check.log_id):
        raise ApplicationError("not_found", "No retained log for this command.", 404)
    path = runtime / "logs" / check.log_id
    try:
        if any(parent.is_symlink() for parent in [path, *path.parents]):
            raise ValueError("Linked log")
        with path.open("rb") as stream:
            if (
                path.stat().st_size != check.log_size
                or hashlib.file_digest(stream, "sha256").hexdigest() != check.log_sha256
            ):
                raise ValueError("Changed log")
    except (OSError, ValueError) as error:
        raise ApplicationError(
            "log_unavailable", "The retained command log is missing or changed.", 409
        ) from error
    return FileResponse(
        path,
        filename="command.log",
        media_type="text/plain",
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )
