"""Proposed-result versions and exact approval, independent of worker execution."""

from typing import Literal

from pydantic import Field, model_validator

from flowfield.execution_models import Correction, Record, WorkerResult


class ResultAction(Record):
    owner: str
    action: Literal[
        "review_result",
        "prepare",
        "correct",
        "revalidate",
        "retry-delivery",
        "request_changes",
        "settings",
        "coordinator",
        "wait",
        "none",
    ]
    label: str
    reason: str = ""
    settings: Literal["workers", "integration"] | None = None


class IndependentAssessment(Record):
    id: str
    status: str
    summary: str = ""
    evidence: str = ""
    request: str = ""


class ResultVersion(Record):
    id: str
    project_id: str
    task_id: str
    task_key: str
    version: int
    revision: int = 1
    run_id: str
    completion: Literal["code", "report"]
    source_commit: str
    report: WorkerResult
    created_at: str
    status: Literal[
        "preparing",
        "ready",
        "blocked",
        "stale",
        "changes_requested",
        "delivering",
        "delivered",
        "cancelled",
    ] = "preparing"
    integration_id: str | None = None
    candidate_commit: str | None = None
    target_branch: str | None = None
    target_before: str | None = None
    settings_revision: int | None = None
    approved_at: str | None = None
    approved_by: str | None = None
    delegated_review_id: str | None = None
    approval_note: str = Field(default="", max_length=8000)
    completed_at: str | None = None
    feedback: str = ""
    problem: str | None = None
    problem_code: str | None = None
    correction: Correction | None = None
    availability_id: str | None = None
    next_action: ResultAction | None = None
    recheck_of: int | None = None
    review_requested: bool = False
    independent_review: IndependentAssessment | None = None


class ResultPage(Record):
    items: list[ResultVersion]
    current_id: str | None = None
    current_run_id: str | None = None
    next_before: int | None = None


class ResultReview(Record):
    expected_revision: int = Field(ge=1)
    candidate_commit: str
    action: Literal["approve", "request_changes"]
    note: str = Field(default="", max_length=8000)
    author: str = Field(default="human", min_length=1, max_length=200)

    @model_validator(mode="after")
    def feedback_required(self) -> "ResultReview":
        if self.action == "request_changes" and not self.note.strip():
            raise ValueError("Describe the requested change.")
        return self


SCHEMA = """
CREATE TABLE result_versions (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    UNIQUE(project_id, task_id, version),
    FOREIGN KEY(project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE INDEX result_work ON result_versions(project_id, status, number);
"""
