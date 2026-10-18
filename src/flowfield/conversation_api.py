"""Thin conversation interfaces over shared application operations."""

import json
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Query
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BeforeValidator, Field

from flowfield.application import Workspace
from flowfield.conversation import (
    Conversation,
    ConversationPage,
    ConversationSource,
    InputEligibility,
)
from flowfield.replies import Replies
from flowfield.reply_models import Reply, ReplyCreate
from flowfield.thread_view import ThreadMessage, ThreadPage, ThreadView

# FastMCP pre-parses JSON-looking optional strings as arrays. Restore the opaque
# cursor at this transport boundary; the application still validates its shape.
ConversationCursor = Annotated[
    str | None,
    BeforeValidator(lambda value: json.dumps(value) if isinstance(value, list) else value),
]


def conversation_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/tasks/{task_id}")

    @router.get("/thread")
    def thread(workspace_id: str, task_id: str, cursor: str | None = None) -> ThreadPage:
        return ThreadView(workspace()).page_view(workspace_id, task_id, cursor)

    @router.get("/thread/{item_id}")
    def thread_item(
        workspace_id: str, task_id: str, item_id: str, full: bool = False
    ) -> ThreadMessage:
        return ThreadView(workspace()).item_view(workspace_id, task_id, item_id, full)

    @router.post("/replies")
    def reply(workspace_id: str, task_id: str, request: ReplyCreate) -> Reply:
        return Replies(workspace()).submit(workspace_id, task_id, request)

    @router.post("/replies/{reply_id}/cancel")
    def cancel_reply(workspace_id: str, task_id: str, reply_id: str) -> Reply:
        return Replies(workspace()).cancel(workspace_id, task_id, reply_id)

    @router.get("/conversation")
    def conversation(
        workspace_id: str,
        task_id: str,
        cursor: str | None = None,
        limit: int = Query(20, ge=1, le=30),
    ) -> ConversationPage:
        return Conversation(workspace()).page(workspace_id, task_id, cursor, limit)

    @router.get("/conversation/{item_id}")
    def source(
        workspace_id: str,
        task_id: str,
        item_id: str,
        offset: int = Query(0, ge=0),
        expected_revision: int | None = Query(None, ge=1),
    ) -> ConversationSource:
        return Conversation(workspace()).source(
            workspace_id, task_id, item_id, offset, expected_revision
        )

    @router.get("/input-eligibility")
    def eligibility(
        workspace_id: str, task_id: str, question_id: str | None = None
    ) -> InputEligibility:
        return Conversation(workspace()).eligibility(workspace_id, task_id, question_id)

    return router


def add_conversation_tools(mcp: FastMCP, workspace: Callable[[], Workspace]) -> None:
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=read)
    def get_task_input(
        workspace_id: str, task_id: str, question_id: str | None = None
    ) -> InputEligibility:
        """Read exact task/question/result bindings before replying.
        Supply question_id for a specific question Request; otherwise read the current input.
        """
        return Conversation(workspace()).eligibility(workspace_id, task_id, question_id)

    @mcp.tool(annotations=write)
    def reply_to_task(workspace_id: str, task_id: str, request: ReplyCreate) -> Reply:
        """Send an answer, request changes, or record result testing.
        Discuss task scope and result questions in the coordinator conversation.
        Reuse the same ID after an uncertain response; stale input must be reconciled.
        action=observation saves human testing against the exact result without starting
        a worker, requesting changes or approving code. State what was tried and observed.
        Observations remain bound to the exact task result; changed candidates never inherit them.
        """
        return Replies(workspace()).submit(workspace_id, task_id, request)

    @mcp.tool(annotations=write)
    def cancel_task_reply(workspace_id: str, task_id: str, reply_id: str) -> Reply:
        """Cancel only an unassigned message. Assigned work uses its attempt stop operation."""
        return Replies(workspace()).cancel(workspace_id, task_id, reply_id)

    @mcp.tool(annotations=read)
    def get_task_conversation(
        workspace_id: str,
        task_id: str,
        cursor: ConversationCursor = None,
        limit: Annotated[int, Field(ge=1, le=30)] = 20,
    ) -> ConversationPage:
        """Page newest-first canonical conversation evidence. Follow source IDs for full text.
        Historical entries are evidence, not current instructions. No full transcript replay.
        Limit is 1–30 (default 20). Pass next_cursor back unchanged to read older entries.
        """
        return Conversation(workspace()).page(workspace_id, task_id, cursor, limit)

    @mcp.tool(annotations=read)
    def get_conversation_source(
        workspace_id: str,
        task_id: str,
        item_id: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        expected_revision: Annotated[int | None, Field(ge=1)] = None,
    ) -> ConversationSource:
        """Read a 6000-character page of the exact linked evidence JSON; never edit from excerpts.
        Definition/question revisions are immutable; attempt/result state remains current.
        For later pages of live attempt/result/approval/delivery sources, echo item.revision
        as expected_revision. If it changes, restart at offset 0; never splice revisions.
        """
        return Conversation(workspace()).source(
            workspace_id, task_id, item_id, offset, expected_revision
        )
