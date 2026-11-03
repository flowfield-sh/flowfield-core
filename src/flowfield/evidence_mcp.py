"""Bounded navigation of the execution evidence available in the application."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from flowfield.adapters.git_review import changed_files, file_patch
from flowfield.check_logs import read as read_log
from flowfield.errors import ApplicationError
from flowfield.execution_evidence import (
    CheckPhase,
    EvidenceSource,
    command_receipts,
    comparison,
    setup_receipts,
)
from flowfield.execution_history import ExecutionHistory
from flowfield.execution_models import CheckResult
from flowfield.run_activity import RunActivity
from flowfield.source_navigation import navigate
from flowfield.supervisor import Supervisor

Offset = Annotated[int, Field(ge=0)]
TextLimit = Annotated[int, Field(ge=1, le=6000)]


def add_evidence_tools(mcp: FastMCP, supervisor: Callable[[], Supervisor]) -> None:
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    @mcp.tool(annotations=read)
    def get_execution(workspace_id: str, task_id: str, execution_id: str) -> dict[str, Any]:
        """Read one exact Worker, validation or delivery execution from get_executions."""
        return (
            ExecutionHistory(supervisor().workspace)
            .get(workspace_id, task_id, execution_id)
            .model_dump()
        )

    @mcp.tool(annotations=read)
    def get_run_activity(
        workspace_id: str, run_id: str, after: Annotated[int, Field(ge=-1)] = -1
    ) -> dict[str, Any]:
        """Read bounded retained Worker activity, continuing from its event cursor."""
        return RunActivity(supervisor().workspace).read(workspace_id, run_id, after).model_dump()

    @mcp.tool(annotations=read)
    async def get_execution_diff(
        workspace_id: str,
        source: EvidenceSource,
        identity: str,
        offset: Offset = 0,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        """Page changed files for an exact run result or integration candidate."""
        return (
            await asyncio.to_thread(
                changed_files,
                *comparison(supervisor(), workspace_id, source, identity),
                offset,
                limit,
            )
        ).model_dump()

    @mcp.tool(annotations=read)
    async def get_execution_patch(
        workspace_id: str,
        source: EvidenceSource,
        identity: str,
        file_id: Annotated[int, Field(ge=0)],
    ) -> dict[str, Any]:
        """Read a bounded file patch from get_execution_diff; preserves truncation notices."""
        return (
            await asyncio.to_thread(
                file_patch, *comparison(supervisor(), workspace_id, source, identity), file_id
            )
        ).model_dump()

    def log_page(
        receipts: tuple[Path, list[CheckResult]], index: int, offset: int, limit: int
    ) -> dict[str, Any]:
        runtime, checks = receipts
        if index >= len(checks):
            raise ApplicationError("not_found", "Command receipt not found.", 404)
        identity = checks[index].log_id
        if not identity:
            raise ApplicationError("not_found", "No retained log for this command.", 404)
        page = navigate(read_log(runtime, checks, identity), offset=offset, limit=limit)
        if page["next_request"]:
            page["next_request"] = {"offset": page["next_offset"], "limit": limit}
        return page

    @mcp.tool(annotations=read)
    async def get_execution_log(
        workspace_id: str,
        source: EvidenceSource,
        identity: str,
        phase: CheckPhase,
        index: Annotated[int, Field(ge=0)],
        offset: Offset = 0,
        limit: TextLimit = 6000,
    ) -> dict[str, Any]:
        """Read exact retained command output by check index from get_run/get_integration.
        Pages are bounded and verified against the receipt's size and hash.
        """
        return await asyncio.to_thread(
            log_page,
            command_receipts(supervisor(), workspace_id, source, identity, phase),
            index,
            offset,
            limit,
        )

    @mcp.tool(annotations=read)
    async def get_setup_log(
        project_id: str,
        check_id: str,
        phase: CheckPhase,
        index: Annotated[int, Field(ge=0)],
        offset: Offset = 0,
        limit: TextLimit = 6000,
    ) -> dict[str, Any]:
        """Page verified retained setup-validation output by command index."""
        return await asyncio.to_thread(
            log_page,
            setup_receipts(supervisor(), project_id, check_id, phase),
            index,
            offset,
            limit,
        )

    @mcp.tool(annotations=read)
    def get_review_attempts(
        workspace_id: str, review_id: str, before: Annotated[int, Field(ge=1)] | None = None
    ) -> dict[str, Any]:
        """Page previous attempts of one independent review, including retained output files."""
        return (
            supervisor()
            .review_execution.reviews.attempt_page(workspace_id, review_id, before)
            .model_dump()
        )

    @mcp.tool(annotations=read)
    def get_review_activity(
        workspace_id: str, review_id: str, attempt_id: str | None = None
    ) -> dict[str, Any]:
        """Read retained Reviewer activity for the latest or an exact earlier attempt."""
        return (
            supervisor()
            .review_execution.reviews.activity(workspace_id, review_id, attempt_id)
            .model_dump()
        )
