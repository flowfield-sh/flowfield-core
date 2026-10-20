"""File assessment uses the same scoped native review owner without verifier tasks or Git."""

import asyncio
import hashlib
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
        original = json.loads(await self.on_tool("read_context", {"section": "files"}))
        sources = json.loads(original["text"])
        copied = {file["id"]: file for file in brief["input_files"]}
        assert len(copied) == 1 + len(sources)
        for source in sources:
            path = Path(copied[source["id"]]["path"])
            assert path.read_text() == "Agreed source evidence"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        context = json.loads(await self.on_tool("read_context", {"section": "result"}))
        output = json.loads(context["text"])["files"][0]
        file = copied[output["id"]]
        assert Path(file["path"]).read_text() == "Agreed report"
        assert output["id"] == file["id"]
        assert output["name"] == file["name"]
        assert output["sha256"] == hashlib.sha256(Path(file["path"]).read_bytes()).hexdigest()
        assert output["size"] == Path(file["path"]).stat().st_size
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
    from unittest.mock import AsyncMock

    monkeypatch.setattr("flowfield.supervisor.Supervisor.validate_agent_choice", AsyncMock())
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
        assert TaskReviews(store).prepare(scope, result.id).id == review.id
        assert TaskReviews(store).latest(scope, current.id).id == review.id
        assert store.task(scope, current.id).status == "done"
        assert TaskReviews(store).check(scope, review.id).status == "decided"
        from flowfield.thread_view import ThreadView

        messages = ThreadView(store).page_view(scope, current.id).items
        reviewing = next(item for item in messages if item.kind == "review")
        assert reviewing.source_id == review.id
        assert reviewing.title == "Reviewing result #1"
        assert reviewing.state.tone == "complete"
        signoff = next(item for item in messages if item.title == "Reviewer signed off")
        delivery = next(item for item in messages if item.kind == "delivery")
        assert reviewing.created_at < signoff.created_at < delivery.created_at
    assert store.projects() == []


def test_file_reviewer_receives_frozen_source_bytes_and_submitted_outputs(tmp_path, monkeypatch):
    store, owner, scope = setup(tmp_path / "state")
    source = task(store, scope, title="Source material")
    delivered = deliver(store, scope, source.id, {"source.txt": "Agreed source evidence"})
    configure(store, owner)
    current = task(store, scope, input_files=[delivered.files[0].id])
    result = deliver(store, scope, current.id, {"report.txt": "Agreed report"})
    install(monkeypatch)
    queue(store, owner)
    asyncio.run(step(Supervisor(store)))
    review = TaskReviews(store).latest(scope, current.id)
    assert review.status == "decided", review.problem
    assert Results(store).get(scope, result.id).status == "delivered"
    assert Outputs(store).content(delivered.files[0]).read_text() == "Agreed source evidence"


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


def test_waiting_report_reviewer_accepts_exact_human_answer(tmp_path, monkeypatch):
    from flowfield.conversation import Conversation
    from flowfield.questions import Questions
    from flowfield.replies import Replies
    from flowfield.reply_models import ReplyBinding, ReplyCreate

    store, owner, scope = setup(tmp_path / "state")
    configure(store, owner)
    current = task(store, scope)
    deliver(store, scope, current.id, {"report.txt": "Agreed report"})
    install(monkeypatch, action="ask_human")
    queue(store, owner)
    asyncio.run(step(Supervisor(store)))
    review = TaskReviews(store).latest(scope, current.id)
    assert review.status == "waiting"
    gate = Conversation(store).eligibility(scope, current.id)
    assert gate.enabled and gate.reason == "answer_expected"
    assert gate.question_id == review.question_id
    request = ReplyCreate(
        id="report-review-answer",
        action="answer",
        body="Use the agreed source notes.",
        binding=ReplyBinding(**gate.model_dump(include=set(ReplyBinding.model_fields))),
    )
    Replies(store).submit(scope, current.id, request)
    saved = Questions(store).get(scope, review.question_id)
    assert saved.answer == request.body
    assert not Conversation(store).eligibility(scope, current.id).enabled
    assert TaskReviews(store).latest(scope, current.id).verdict is None
