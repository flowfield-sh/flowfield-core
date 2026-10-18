"""File assessment uses the same scoped native review owner without verifier tasks or Git."""

import asyncio
import json
from pathlib import Path

import pytest
from fake_worker import FakeWorker
from file_delivery_fixtures import deliver, setup, task

from flowfield.agent_models import AgentChoice
from flowfield.execution_models import QueueEdit, WorkerResult
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.results import Results
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.supervisor import Supervisor
from flowfield.task_reviews import TaskReviews
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
                "request": "" if self.action == "approve" else "Include the agreed source notes.",
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


def configure(store, owner):
    Reviewers(store).configure(
        owner.id,
        ReviewerEdit(
            expected_revision=2, enabled=True, selection=AgentChoice(model="review-fixture")
        ),
    )


def queue(store, owner):
    spaces = Workspaces(store)
    settings = spaces.workers(owner.id)
    spaces.queue(owner.id, QueueEdit(expected_revision=settings.revision, enabled=True))


async def step(service):
    execution = service.review_execution
    await execution.schedule()
    await asyncio.gather(*list(service.jobs.values()))


@pytest.mark.parametrize(
    "action,mutate", [("approve", False), ("request_changes", False), ("approve", True)]
)
def test_report_review_gates_completion_and_preserves_files(tmp_path, monkeypatch, action, mutate):
    store, owner, scope = setup(tmp_path / "state")
    configure(store, owner)
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
        from flowfield.thread_view import ThreadView

        messages = ThreadView(store).page_view(scope, current.id).items
        signoff = next(item for item in messages if item.title == "Reviewer signed off")
        delivery = next(item for item in messages if item.kind == "delivery")
        assert not signoff.technical and signoff.created_at < delivery.created_at
    assert store.projects() == []


def test_partial_file_review_requests_followup_without_approving(tmp_path, monkeypatch):
    store, owner, scope = setup(tmp_path / "state")
    configure(store, owner)
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
