"""Candidate-bound reviewer and implementer clarification tools; no delivery authority."""

import json
from typing import Any, Literal

from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import Field

from flowfield.agent_tools import ScopedTools
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.task_reviews import ReviewVerdict, TaskReview, TaskReviews
from flowfield.worker_tools import ReadContext


class ReviewResponse(Record):
    action: Literal["answer", "ask_human"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=8000)
    request: str = Field(default="", max_length=8000)


class ReviewBridge:
    def __init__(self, reviews: TaskReviews, review: TaskReview, attempt: str, role: str):
        self.reviews, self.review, self.attempt, self.role = reviews, review, attempt, role
        self.decision: ReviewVerdict | ReviewResponse | None = None

    def scope(self) -> ScopedTools:
        model = ReviewVerdict if self.role == "reviewer" else ReviewResponse
        operation = "submit_review" if self.role == "reviewer" else "answer_review"
        tools = [
            Tool(
                name="read_context",
                description="Read a bounded page of frozen assignment, result, validation or "
                "review exchange evidence. Available section names are in the brief.",
                inputSchema=ReadContext.model_json_schema(),
                annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
            ),
            Tool(
                name=operation,
                description="Record this round's conclusion and evidence, then end your turn. "
                "Approval is an assessment only. Corrections must stay within the agreed task; "
                "scope, intent or access changes require ask_human. Optional suggestions must "
                "not become blocking requests. A clarification answer cannot change code.",
                inputSchema=model.model_json_schema(),
                annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
            ),
        ]

        async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
            self.reviews.access(self.review.project_id, self.review.id, self.attempt)
            if self.decision:
                raise ApplicationError("review_closed", "The round is saved; end your turn.", 409)
            if name == "read_context":
                request = ReadContext.model_validate(arguments)
                text = self.reviews.read_context(self.review, request.section)
                if request.offset > len(text):
                    raise ApplicationError("invalid_offset", "Offset exceeds source length.")
                end = min(len(text), request.offset + 6000)
                result = json.dumps(
                    {
                        "text": text[request.offset : end],
                        "total_chars": len(text),
                        "next_offset": end if end < len(text) else None,
                    }
                )
            else:
                self.decision = model.model_validate(arguments)
                if self.decision.action == "ask_human" and not self.decision.request.strip():
                    self.decision = None
                    raise ApplicationError("request_required", "State the human question.")
                self.reviews.save_decision(
                    self.review.project_id, self.review.id, self.attempt, self.decision.model_dump()
                )
                result = (
                    "Round retained. End your turn; the service will verify cleanup and bindings."
                )
            return CallToolResult(content=[TextContent(type="text", text=result)])

        return ScopedTools(tools, call, parallel_reads=True)
