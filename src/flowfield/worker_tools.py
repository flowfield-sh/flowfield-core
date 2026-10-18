"""Run-bound workflow operations shared by managed harness adapters."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field

from flowfield.context_pages import ReadContext
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import Record, Run, WorkerResult, WorkerSubmission
from flowfield.questions import QuestionCreate
from flowfield.run_activity import ActivityUpdate


class SearchContext(Record):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class WorkerQuestion(Record):
    question: str = Field(min_length=1, max_length=200)
    context: str = Field(min_length=1, max_length=8000)
    recommendation: str = Field(min_length=1, max_length=8000)


def worker_tools() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": name,
            "description": description,
            "inputSchema": model.model_json_schema(),
        }
        for name, description, model in (
            (
                "run_project_checks",
                "Run the saved project checks on this worker checkout using "
                "human-granted Flowfield host access. Takes no command arguments. "
                "Timeout, cancellation and bounded output are service-owned. Final "
                "candidate validation remains separate.",
                Record,
            ),
            (
                "read_context",
                (
                    "Read a 6000-character page of an assignment section. "
                    "project_checks return current retained evidence; other sections are frozen. "
                    "Available sections are listed in the initial brief."
                ),
                ReadContext,
            ),
            (
                "search_context",
                "Search only the frozen assignment. Returns snippets and section references; "
                "read_context retrieves complete evidence. Cannot discover later project changes.",
                SearchContext,
            ),
            (
                "ask_question",
                (
                    "Ask a blocking human question on this task, then end your turn. "
                    "The service preserves your work and continues with the saved answer "
                    "in a fresh attempt when the queue and assignment allow it."
                ),
                WorkerQuestion,
            ),
            (
                "submit_result",
                (
                    "Submit this attempt's summary, checks actually run and "
                    "limitations. Keep summary focused on the user's outcome, technical "
                    "proof in checks, and only unresolved material caveats in limitations. "
                    "Explicitly choose complete or partial against the entire "
                    "agreed outcome and name remaining_work for partial progress. "
                    "Code delivery follows explicit approval policy; complete unchanged-tree "
                    "reports finish after configured review. Put deliverable files in "
                    "FLOWFIELD_OUTPUT_DIR "
                    "and list their relative paths and descriptions in files. The service "
                    "retains those bytes independently of attempt cleanup. At most 32 files, "
                    "128 MiB each and 512 MiB total."
                ),
                WorkerSubmission,
            ),
        )
    ]
    return tools


class WorkerBridge:
    """Fixed server-side run identity; worker input cannot select another task/project."""

    def __init__(
        self,
        execution: Execution,
        run: Run,
        client: Any,
        checks: Callable[[], Awaitable[str]] | None = None,
    ):
        self.execution, self.run, self.client = execution, run, client
        self.checks = checks
        self.result: WorkerResult | None = None
        self.question_id: str | None = None

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        self.execution.worker_access(self.run.workspace_id, self.run.id)
        if self.question_id or self.result:
            raise ApplicationError(
                "report_closed",
                "The report is saved; end this turn. Further work needs a new attempt.",
            )
        activity = getattr(self.client, "on_activity", None)
        titles = {
            "run_project_checks": "Running project checks",
            "read_context": "Reading task context",
            "search_context": "Searching task context",
            "ask_question": "Asking a question",
            "submit_result": "Submitting an outcome",
        }
        if activity and name in titles:
            from uuid import uuid4

            activity(ActivityUpdate(key=uuid4().hex, kind="tool", text=titles[name]))
        if name == "run_project_checks":
            Record.model_validate(arguments)
            if not self.checks:
                raise ApplicationError(
                    "checks_unavailable",
                    "Project check execution is unavailable for this attempt.",
                    409,
                )
            return await self.checks()
        if name == "read_context":
            request = ReadContext.model_validate(arguments)
            text = self.execution.context_section(
                self.run.workspace_id, self.run.id, request.section
            )
            return request.page(text)
        if name == "search_context":
            from flowfield.search import assignment_search

            query = SearchContext.model_validate(arguments)
            return json.dumps(
                assignment_search(
                    self.execution.assignment(self.run.workspace_id, self.run.id),
                    query.query,
                    query.limit,
                )
            )
        if name == "ask_question":
            request_question = WorkerQuestion.model_validate(arguments)
            question = self.execution.ask_question(
                self.run.workspace_id,
                self.run.id,
                QuestionCreate(
                    **request_question.model_dump(),
                    task_id=self.run.task_id,
                    blocking_scope=("The worker needs this answer before continuing the task."),
                    author=f"worker:{self.run.id[:8]}",
                ),
            )
            self.question_id = question.id
            return (
                "Question recorded in Inbox. End your turn so the service can preserve "
                "your work. A saved answer will be supplied to an eligible fresh attempt."
            )
        if name == "submit_result":
            result = WorkerSubmission.model_validate(arguments)
            self.execution.report_result(self.run.workspace_id, self.run.id, result)
            self.result = result
            return (
                "Result received for this attempt. End your turn so the service "
                "can stop execution and finish capturing this result."
            )
        raise ApplicationError(
            "worker_operation_denied", "This operation is outside the worker's task scope.", 403
        )
