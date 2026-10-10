"""Run-bound workflow operations shared by managed harness adapters."""

import json
from typing import Any

from pydantic import Field

from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import Record, Run, WorkerResult, WorkerSubmission
from flowfield.questions import QuestionCreate
from flowfield.run_activity import ActivityUpdate
from flowfield.stage_models import StageUpdate
from flowfield.stages import Stages


class ReadContext(Record):
    section: str
    offset: int = Field(default=0, ge=0)


class SearchContext(Record):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class WorkerQuestion(Record):
    question: str = Field(min_length=1, max_length=200)
    context: str = Field(min_length=1, max_length=8000)
    recommendation: str = Field(min_length=1, max_length=8000)


def worker_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "inputSchema": model.model_json_schema(),
        }
        for name, description, model in (
            (
                "read_context",
                (
                    "Read a 6000-character page of the frozen assignment section. "
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
                "update_stages",
                "Report broad work phases, not file edits or implementation checklists. "
                "Keep human approval/integration outside agent stages. Copy every existing "
                "stage id and outcome verbatim; change status and put evidence in reason. "
                "You may add stages, but cannot reword or remove existing outcomes. "
                "Stages never grant approval or complete the task. "
                "Use the frozen stages revision first, then the revision returned by this tool.",
                StageUpdate,
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
                    "limitations. Explicitly choose complete or partial against the entire "
                    "agreed outcome and name remaining_work for partial progress. Before a "
                    "complete result, update any unfinished stages to reflect actual progress. "
                    "Code still requires human approval; complete unchanged-tree reports finish "
                    "on delivery."
                ),
                WorkerSubmission,
            ),
        )
    ]


class WorkerBridge:
    """Fixed server-side run identity; worker input cannot select another task/project."""

    def __init__(self, execution: Execution, run: Run, client: Any):
        self.execution, self.run, self.client = execution, run, client
        self.result: WorkerResult | None = None
        self.question_id: str | None = None

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        self.execution.worker_access(self.run.project_id, self.run.id)
        if self.question_id or self.result:
            raise ApplicationError(
                "report_closed",
                "The report is saved; end this turn. Further work needs a new attempt.",
            )
        activity = getattr(self.client, "on_activity", None)
        titles = {
            "read_context": "Reading task context",
            "search_context": "Searching task context",
            "update_stages": "Updating progress",
            "ask_question": "Asking a question",
            "submit_result": "Submitting an outcome",
        }
        if activity and name in titles:
            from uuid import uuid4

            activity(ActivityUpdate(key=uuid4().hex, kind="tool", text=titles[name]))
        if name == "update_stages":
            return (
                Stages(self.execution.workspace)
                .update(
                    self.run.project_id,
                    self.run.task_id,
                    StageUpdate.model_validate(arguments),
                    run_id=self.run.id,
                )
                .model_dump_json()
            )
        if name == "read_context":
            request = ReadContext.model_validate(arguments)
            text = self.execution.context_section(self.run.project_id, self.run.id, request.section)
            if request.offset > len(text):
                raise ApplicationError("invalid_offset", "Offset exceeds source length.")
            end = min(len(text), request.offset + 6000)
            return json.dumps(
                {
                    "text": text[request.offset : end],
                    "total_chars": len(text),
                    "next_offset": end if end < len(text) else None,
                }
            )
        if name == "search_context":
            from flowfield.search import assignment_search

            query = SearchContext.model_validate(arguments)
            return json.dumps(
                assignment_search(
                    self.execution.assignment(self.run.project_id, self.run.id),
                    query.query,
                    query.limit,
                )
            )
        if name == "ask_question":
            request_question = WorkerQuestion.model_validate(arguments)
            question = self.execution.ask_question(
                self.run.project_id,
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
            self.execution.report_result(self.run.project_id, self.run.id, result)
            self.result = result
            return (
                "Result received for this attempt. End your turn so the service "
                "can stop execution and capture code for review."
            )
        raise ApplicationError(
            "worker_operation_denied", "This operation is outside the worker's task scope.", 403
        )
