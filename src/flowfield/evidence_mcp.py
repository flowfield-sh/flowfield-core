"""Bounded navigation of the execution evidence available in the application."""

import asyncio
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from flowfield.adapters.git_review import changed_files, file_patch
from flowfield.check_logs import page as log_page
from flowfield.execution_evidence import (
    CheckPhase,
    EvidenceSource,
    command_receipts,
    comparison,
    setup_receipts,
)
from flowfield.execution_history import ExecutionHistory
from flowfield.run_activity import RunActivity
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
