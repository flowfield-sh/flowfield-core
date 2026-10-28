"""Candidate-bound reviewer and implementer clarification tools; no delivery authority."""

from pathlib import Path
from typing import Any, Literal

from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import Field

from flowfield.agent_tools import ScopedTools
from flowfield.context_pages import ReadContext
from flowfield.errors import ApplicationError
from flowfield.execution_models import Record
from flowfield.guidance_models import GuidanceDecision
from flowfield.output_models import OutputSelection
from flowfield.task_evidence import EvidenceFileRequest
from flowfield.task_reviews import ReviewVerdict, TaskReview, TaskReviews


class ReviewResponse(Record):
    action: Literal["answer", "ask_human"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=8000)
    request: str = Field(default="", max_length=8000)


class VerificationResponse(ReviewResponse):
    files: list[OutputSelection] = Field(default_factory=list, max_length=32)


class ReviewBridge:
    def __init__(self, reviews: TaskReviews, review: TaskReview, attempt: str, role: str):
        self.reviews, self.review, self.attempt, self.role = reviews, review, attempt, role
        self.decision: ReviewVerdict | ReviewResponse | GuidanceDecision | None = None

    def scope(self) -> ScopedTools:
        model = (
            GuidanceDecision
            if self.review.guidance
            else ReviewVerdict
            if self.role == "reviewer"
            else VerificationResponse
            if self.review.verification
            else ReviewResponse
        )
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
        if not self.review.guidance:
            tools.append(
                Tool(
                    name="read_evidence",
                    description="Retrieve an exact current or historical task output "
                    "into this attempt's read-only evidence area. File IDs and provenance are in "
                    "result, prior_result:<id> and observation attachment URLs. "
                    "Does not change current deliverables or code.",
                    inputSchema=EvidenceFileRequest.model_json_schema(),
                    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
                )
            )

        async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
            self.reviews.access(self.review.workspace_id, self.review.id, self.attempt)
            if self.decision:
                raise ApplicationError("review_closed", "The round is saved; end your turn.", 409)
            if name == "read_context":
                request = ReadContext.model_validate(arguments)
                text = self.reviews.read_context(self.review, request.section)
                result = request.page(text)
                if self.review.result_id and request.section.startswith("observation:"):
                    from flowfield.review_observations import record_read

                    with self.reviews.store.connection(write=True, notify=False) as db:
                        record_read(
                            db,
                            self.attempt,
                            request.section.removeprefix("observation:"),
                            request.offset,
                            len(text),
                        )
            elif name == "read_evidence" and not self.review.guidance:
                from flowfield.results import Results
                from flowfield.task_evidence import read_file

                query_file = EvidenceFileRequest.model_validate(arguments)
                assert self.review.result_id
                current = Results(self.reviews.store).get(
                    self.review.workspace_id, self.review.result_id
                )
                metadata = self.reviews.attempt(self.attempt)
                if not metadata.get("runtime"):
                    raise ApplicationError(
                        "evidence_unavailable", "The review has no evidence directory.", 409
                    )
                result = read_file(
                    self.reviews.store,
                    self.review.workspace_id,
                    self.review.task_id,
                    query_file.file_id,
                    Path(metadata["runtime"]) / "evidence",
                    result_before=current.version + 1,
                )
            else:
                decision = model.model_validate(arguments)
                if decision.action == "ask_human" and not decision.request.strip():
                    raise ApplicationError("request_required", "State the human question.")
                if (
                    decision.action == "approve"
                    and self.review.binding
                    and self.review.binding.outcome == "partial"
                ):
                    raise ApplicationError(
                        "partial_outcome",
                        "Request the remaining work or ask a focused question; "
                        "partial outcomes cannot be approved.",
                    )
                if isinstance(decision, ReviewVerdict):
                    self.reviews.validate_decision(self.review, decision)
                self.reviews.save_decision(
                    self.review.workspace_id, self.review.id, self.attempt, decision.model_dump()
                )
                self.decision = decision
                result = (
                    "Round retained. End your turn; the service will verify cleanup and bindings."
                )
            return CallToolResult(content=[TextContent(type="text", text=result)])

        return ScopedTools(tools, call, parallel_reads=True)
