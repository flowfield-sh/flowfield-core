"""File assessment uses the same scoped native review owner without verifier tasks or Git."""

import asyncio
import json
from pathlib import Path

import pytest
from fake_worker import FakeWorker
from file_delivery_fixtures import deliver, setup, task

from flowfield.agent_models import AgentChoice
from flowfield.application import MilestoneCreate
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, WorkerResult
from flowfield.milestone_review_status import status
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.milestones import Milestones
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.results import Results
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.supervisor import Supervisor
from flowfield.task_reviews import TaskReviews
from flowfield.verification_models import VerificationReview
from flowfield.verification_plans import VerificationPlanEdit, VerificationPlans
from flowfield.workspaces import Workspaces


class FileReviewer(FakeWorker):
    action = "approve"
    mutate = False
    remove = None
    partial = False

    async def run(self, model, effort, prompt, tools):
        brief = json.loads(prompt)
        assert not (self.cwd / ".git").exists()
        assert len(brief["input_files"]) == 1
        file = brief["input_files"][0]
        assert Path(file["path"]).read_text() == "Agreed report"
        if self.mutate:
            Path(file["path"]).chmod(0o600)
            Path(file["path"]).write_text("Changed inspection copy")
        if brief["purpose"] == "milestone_reviewer":
            context = json.loads(await self.on_tool("read_context", {"section": "verification"}))
            verification = json.loads(context["text"])
            await self.on_tool(
                "submit_milestone_review",
                {
                    "action": "report",
                    "summary": "The report meets the milestone.",
                    "evidence": "Read the retained report and agreed criteria.",
                    "report": {
                        "verification_id": verification["id"],
                        "coverage": "complete",
                        "coverage_notes": "The report includes all agreed findings.",
                        "findings": [
                            {
                                "check": 1,
                                "verdict": "passed" if self.action == "approve" else "failed",
                                "evidence": "Read the agreed report.",
                            }
                        ],
                        "output_ids": [file["id"]],
                    },
                    **(
                        {
                            "correction_task_id": verification["tasks"][0]["task_id"],
                            "correction_check": 1,
                            "request": "Include the agreed source notes.",
                        }
                        if self.action == "request_changes"
                        else {}
                    ),
                },
            )
        else:
            if self.partial:
                with pytest.raises(AssertionError, match="partial_outcome"):
                    await self.on_tool(
                        "submit_review",
                        {
                            "action": "approve",
                            "summary": "Cannot approve unfinished work",
                            "evidence": "The report is partial.",
                        },
                    )
            await self.on_tool(
                "submit_review",
                {
                    "action": self.action,
                    "summary": "Report checked",
                    "evidence": "Read the exact retained report.",
                    "request": ""
                    if self.action == "approve"
                    else "Include the agreed source notes.",
                },
            )
        if self.remove:
            self.remove.unlink()
        return {"status": "completed"}


def install(monkeypatch, *, action="approve", mutate=False, remove=None, partial=False):
    FileReviewer.action, FileReviewer.mutate = action, mutate
    FileReviewer.remove, FileReviewer.partial = remove, partial
    monkeypatch.setattr(
        "flowfield.native_review_execution.create",
        lambda choice, directory, cwd, environment, **kw: FileReviewer(
            directory, cwd, environment, **kw
        ),
    )


def configure(store, owner, purpose):
    Reviewers(store).configure(
        owner.id,
        purpose,
        ReviewerEdit(
            expected_revision=1, enabled=True, selection=AgentChoice(model="review-fixture")
        ),
    )


def queue(store, owner):
    spaces = Workspaces(store)
    settings = spaces.workers(owner.id)
    spaces.queue(owner.id, QueueEdit(expected_revision=settings.revision, enabled=True))


async def step(service, milestone=False):
    execution = service.milestone_review_execution if milestone else service.review_execution
    await execution.schedule()
    await asyncio.gather(*list(service.jobs.values()))


@pytest.mark.parametrize(
    "action,mutate", [("approve", False), ("request_changes", False), ("approve", True)]
)
def test_report_review_gates_completion_and_preserves_files(tmp_path, monkeypatch, action, mutate):
    store, owner, scope = setup(tmp_path / "state")
    configure(store, owner, "task")
    current = task(store, scope)
    result = deliver(store, scope, current.id, {"report.txt": "Agreed report"})
    assert result.status == "ready" and result.next_action.owner == "reviewer"
    assert store.task(scope, current.id).status != "done"
    install(monkeypatch, action=action, mutate=mutate)
    queue(store, owner)
    service = Supervisor(store)
    asyncio.run(step(service))
    review = TaskReviews(store).latest(scope, current.id)
    result = Results(store).get(scope, result.id)
    assert review.status == ("failed" if mutate else "decided"), review.problem
    assert result.status == (
        "ready" if mutate else "delivered" if action == "approve" else "changes_requested"
    )
    assert result.approved_at is None
    assert Outputs(store).content(result.files[0]).read_text() == "Agreed report"
    if action == "approve" and not mutate:
        assert store.task(scope, current.id).status == "done"
        assert TaskReviews(store).check(scope, review.id).status == "decided"
    assert store.projects() == []


@pytest.mark.parametrize("action", ["approve", "request_changes", "missing_file"])
def test_milestone_files_reach_acceptance_without_synthetic_task(tmp_path, monkeypatch, action):
    store, owner, scope = setup(tmp_path / "state")
    configure(store, owner, "milestone")
    milestone = Milestones(store).create(
        owner.id, MilestoneCreate(title="Research", acceptance_criteria="An agreed report")
    )
    current = task(store, scope, milestone_id=milestone.id)
    delivered = deliver(store, scope, current.id, {"report.txt": "Agreed report"})
    VerificationPlans(store).save(
        owner.id,
        milestone.id,
        VerificationPlanEdit(
            expected_revision=1,
            milestone_revision=1,
            checks=["The report covers the agreed findings"],
            instructions="Read the retained report.",
        ),
    )
    install(
        monkeypatch,
        action="approve" if action == "missing_file" else action,
        remove=Outputs(store).content(delivered.files[0]) if action == "missing_file" else None,
    )
    queue(store, owner)
    service = Supervisor(store)
    asyncio.run(step(service, True))
    with store.connection() as db:
        row = db.execute(
            "SELECT id FROM milestone_verifications WHERE milestone_id=?", (milestone.id,)
        ).fetchone()
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    records = MilestoneVerifications(store)
    value = records.get(owner.id, row[0])
    if action == "missing_file":
        review = service.milestone_review_execution.reviews.get(scope, value.review_id)
        assert review.status == "stale"
        assert value.status != "ready"
        return
    if action == "request_changes":
        assert value.status == "failed"
        assert store.task(scope, current.id).status == "up_next"
        assert Results(store).get(scope, delivered.id).status == "delivered"
        assert Outputs(store).content(delivered.files[0]).read_text() == "Agreed report"
        return
    assert value.status == "ready", value.problem
    assert value.task_id is None and value.promotion_id is None
    assert len(value.files) == 1 and value.review_id
    reviews = service.milestone_review_execution.reviews
    review = reviews.get(scope, value.review_id)
    assert reviews.read_context(review, f"contribution:{delivered.id}")
    ambiguous = value.model_copy(deep=True)
    ambiguous.tasks.append(value.tasks[0].model_copy(update={"project_id": "another-project"}))
    decision = review.verdict.model_copy(deep=True)
    decision.correction_task_id = current.id
    decision.correction_check = 1
    decision.request = "Correct the agreed report."
    decision.report.findings[0].verdict = "failed"
    with store.connection(write=True) as db:
        with pytest.raises(ApplicationError, match="unambiguous"):
            reviews._correct(db, review, ambiguous, decision)
    assert (
        status(store, owner.id, milestone.id).message
        == "The combined outcome is ready for your review."
    )
    records.review(
        owner.id, value.id, VerificationReview(expected_revision=value.revision, action="accept")
    )
    assert records.get(owner.id, value.id).status == "accepted"
    assert store.projects() == []


def test_partial_file_review_requests_followup_without_approving(tmp_path, monkeypatch):
    store, owner, scope = setup(tmp_path / "state")
    configure(store, owner, "task")
    current = task(store, scope)
    result = deliver(
        store,
        scope,
        current.id,
        {"report.txt": "Agreed report"},
        WorkerResult(
            summary="Report is drafted, source notes remain.",
            checks="Read the draft.",
            outcome="partial",
            remaining_work="Include the agreed source notes.",
            files=[OutputSelection(path="report.txt")],
        ),
    )
    assert result.status == "blocked"
    install(monkeypatch, action="request_changes", partial=True)
    queue(store, owner)
    asyncio.run(step(Supervisor(store)))
    review = TaskReviews(store).latest(scope, current.id)
    assert review.status == "decided", review.problem
    assert review.verdict.action == "request_changes"
    assert store.task(scope, current.id).status == "up_next"
    assert Outputs(store).content(result.files[0]).read_text() == "Agreed report"
