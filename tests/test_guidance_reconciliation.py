"""Current guidance at execution/review boundaries, with real scoped MCP and no models."""

import asyncio
import json

import pytest
from execution_fixtures import fixture as execution_fixture
from result_fixtures import approve, current
from review_execution_fixtures import Reviewer, install, round_trip
from task_reviews_fixtures import configured
from worker_documents_fixtures import defaults

from flowfield.agent_models import AgentChoice
from flowfield.application import TaskCreate, TaskPreparation
from flowfield.documents import DocumentCreate, DocumentEdit, Documents
from flowfield.errors import ApplicationError
from flowfield.guidance_reconciliation import pending, prepare
from flowfield.questions import QuestionAnswer, Questions
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.supervisor import Supervisor
from flowfield.task_reviews import TaskReviews


def planning(tmp_path):
    """Guidance planning needs no Git candidate, worktree or result validation."""
    execution = execution_fixture(tmp_path, count=0)
    Reviewers(execution.workspace).configure(
        "harbor",
        ReviewerEdit(
            expected_revision=2, enabled=True, selection=AgentChoice(model="review-fixture")
        ),
    )
    return Supervisor(execution.workspace), TaskReviews(execution.workspace)


def planned_task(service):
    return service.workspace.create_task(
        "harbor",
        TaskCreate(
            title="Investigate labels",
            body="Identify unclear labels and report recommendations.",
            status="up_next",
            project_id="harbor",
            preparation=TaskPreparation(completion="report"),
        ),
    )


class GuidanceReviewer(Reviewer):
    proposal = {}

    async def run(self, model, effort, prompt, tools):
        brief = json.loads(prompt)
        if brief["purpose"] != "guidance_reconciliation":
            return await super().run(model, effort, prompt, tools)
        self.observed.append(brief)
        await self.on_tool("read_context", {"section": "current_guidance"})
        await self.on_tool(
            "submit_review",
            {
                "action": "adopt",
                "summary": "Aligned with current guidance",
                "evidence": "Compared the changed guidance with the task and retained result.",
                **self.proposal,
            },
        )
        return {"status": "completed"}


def peers(monkeypatch, proposal=None):
    install(monkeypatch)
    GuidanceReviewer.proposal = proposal or {}
    monkeypatch.setattr(
        "flowfield.native_review_execution.create",
        lambda choice, directory, cwd, environment, **kw: GuidanceReviewer(
            directory, cwd, environment, **kw
        ),
    )


def change(store, body="Use clear labels."):
    documents = Documents(store)
    original = documents.get("harbor", "guidance")
    return documents.edit(
        "harbor", "guidance", DocumentEdit(expected_revision=original.revision, body=body)
    )


def test_preflight_refreshes_latest_guidance_and_then_claims(tmp_path, monkeypatch):
    service, reviews = planning(tmp_path)
    task = service.workspace.create_task(
        "harbor",
        TaskCreate(
            title="Investigate accessibility",
            body="Identify unclear labels",
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    changed = change(service.workspace)
    assert service.execution.claim("harbor", "", {}, task_id=task.id) is None
    peers(monkeypatch)
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", task.id)
    assert review.guidance and review.status == "decided"
    from flowfield.thread_view import ThreadView

    events = ThreadView(service.workspace).page_view("harbor", task.id).items
    assert any(event.title == "Reviewing updated guidance" for event in events)
    saved = service.workspace.task("harbor", task.id)
    assert saved.publication.documents[0].revision == changed.revision
    run = service.execution.claim("harbor", "", {}, task_id=task.id)
    assert run and run.documents == saved.publication.documents
    assert service.execution.assignment("harbor", run.id)["document:guidance"] == changed.body


def test_awaiting_approval_reconciles_then_requires_new_assessment(tmp_path, monkeypatch):
    service, _, run, _, reviews = configured(tmp_path)
    peers(monkeypatch)
    asyncio.run(round_trip(service))
    old = current(service)
    frozen = service.execution.assignment("harbor", run.id)
    changed = change(service.workspace)
    with pytest.raises(ApplicationError, match="guidance"):
        approve(service, old)
    assert current(service).next_action.action == "wait"
    asyncio.run(round_trip(service))
    reconciliation = reviews.latest("harbor", run.task_id)
    assert reconciliation.guidance and reconciliation.status == "decided"
    refreshed = current(service)
    assert refreshed.version > old.version and not refreshed.approved_at
    assert refreshed.guidance_documents[0].revision == changed.revision
    assert service.execution.assignment("harbor", run.id) == frozen
    service.results.process("harbor")
    asyncio.run(round_trip(service))
    assert reviews.latest("harbor", run.task_id).verdict.action == "approve"
    with pytest.raises(ApplicationError):
        approve(service, old)
    approve(service, current(service))
    service.results.process("harbor")
    assert current(service).status == "delivered"
    change(service.workspace, "A later requirement")
    asyncio.run(round_trip(service))
    assert service.workspace.task("harbor", run.task_id).status == "done"
    assert current(service).guidance_documents[0].revision == changed.revision
    assert reviews.check("harbor", reconciliation.id).status == "decided"


@pytest.mark.parametrize("completion", ["code", "report"])
def test_post_work_reconciliation_waits_for_code_validation_only(tmp_path, monkeypatch, completion):
    from result_fixtures import fixture

    from flowfield.agent_models import AgentChoice
    from flowfield.execution_models import QueueEdit
    from flowfield.reviewer_settings import ReviewerEdit, Reviewers
    from flowfield.task_reviews import TaskReviews

    service, _, run = fixture(tmp_path, completion=completion, changed=completion == "code")
    Reviewers(service.workspace).configure(
        "harbor",
        ReviewerEdit(
            expected_revision=2, enabled=True, selection=AgentChoice(model="review-fixture")
        ),
    )
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    reviews = TaskReviews(service.workspace)
    change(service.workspace)
    peers(monkeypatch)
    assert current(service).status == "preparing"
    if completion == "code":
        asyncio.run(round_trip(service))
        assert reviews.latest("harbor", run.task_id) is None
        assert not GuidanceReviewer.observed
        service.results.process("harbor")
        assert current(service).status == "ready"
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.guidance and review.status == "decided"
    assert len(GuidanceReviewer.observed) == 1
    assert current(service).version == 2


def test_changed_definition_queues_retained_implementation_with_new_assignment(
    tmp_path, monkeypatch
):
    service, _, run, _, reviews = configured(tmp_path)
    change(service.workspace)
    peers(
        monkeypatch,
        {
            "body": "Deliver the result with accessible labels.",
            "request": "Make the result labels accessible.",
            "rework": True,
        },
    )
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "decided"
    task = service.workspace.task("harbor", run.task_id)
    assert task.status == "up_next" and task.readiness == "ready"
    assert task.agreement_revision > run.agreement_revision
    followup = service.execution.claim("harbor", run.base_commit, {}, task_id=task.id)
    assert followup and followup.base_commit == run.result_commit
    assert followup.documents == task.publication.documents
    assert service.execution.assignment("harbor", followup.id)["description"] == task.body
    service.execution.finish(
        "harbor", followup.id, "waiting_for_input", input_checkpoint=run.result_commit
    )
    change(service.workspace, "Use concise accessible labels.")
    next_review = prepare(reviews, "harbor", task.id)
    assert next_review.result_id is None
    assert next_review.guidance.run_id == followup.id
    assert next_review.guidance.candidate_commit == run.result_commit


def test_editorial_definition_reuses_code_with_one_revision_and_fresh_review(tmp_path, monkeypatch):
    from flowfield.execution_models import RunAction
    from flowfield.thread_view import ThreadView

    service, _, run, _, reviews = configured(tmp_path)
    store = service.workspace
    before = store.task("harbor", run.task_id)
    old = current(service)
    frozen = service.execution.assignment("harbor", run.id)
    change(store)
    peers(
        monkeypatch,
        {
            "body": "Deliver the existing result with clear labels.",
            "acceptance_criteria": "Labels are concise and accessible.",
            "rework": False,
        },
    )
    asyncio.run(round_trip(service))
    after = store.task("harbor", run.task_id)
    reconciliation = reviews.latest("harbor", run.task_id)
    assert after.revision == reconciliation.guidance.task_revision + 1
    assert after.publication.task_revision == after.revision
    assert after.publication.agreement_revision == after.agreement_revision
    events = ThreadView(store).page_view("harbor", run.task_id).items
    updates = [e for e in events if e.kind == "definition" and e.revision > before.revision]
    assert len(updates) == 1
    assert service.execution.assignment("harbor", run.id) == frozen
    refreshed = current(service)
    assert refreshed.version == 2 and refreshed.run_id == old.run_id
    assert refreshed.source_commit == old.source_commit
    assert refreshed.reconciled_task_revision == after.revision
    assert service.execution.claim("harbor", run.base_commit, {}, task_id=run.task_id) is None
    service.results.process("harbor")
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.verdict.action == "approve"
    assignment = reviews.assignment(review)
    assert assignment["description"] == after.body
    assert assignment["criteria"] == after.acceptance_criteria
    assert json.loads(assignment["agreement"])["agreement_revision"] == after.agreement_revision
    with pytest.raises(ApplicationError):
        approve(service, old)
    # Destination refresh retains the adopted definition and requires another assessment.
    refreshed = current(service)
    refreshed = service.results.reprepare(
        "harbor", refreshed.id, RunAction(expected_revision=refreshed.revision)
    )
    assert refreshed.reconciled_task_revision == after.revision
    service.results.process("harbor")
    asyncio.run(round_trip(service))
    approve(service, current(service))
    service.results.process("harbor")
    delivered = current(service)
    assert delivered.status == "delivered" and delivered.next_action.label == "Delivered"
    assert store.task("harbor", run.task_id).status == "done"
    child = store.create_task("harbor", TaskCreate(title="Follow up", dependencies=[run.task_id]))
    assert not store.task("harbor", child.id).blocked_by
    assert service.execution.assignment("harbor", run.id) == frozen
    change(store, "Future guidance does not alter completed work.")
    assert prepare(reviews, "harbor", run.task_id) is None


def test_later_definition_change_cannot_reuse_an_editorial_adoption(tmp_path, monkeypatch):
    from flowfield.application import TaskEdit

    service, _, run, _, reviews = configured(tmp_path)
    change(service.workspace)
    peers(monkeypatch, {"acceptance_criteria": "Clarified existing behavior.", "rework": False})
    asyncio.run(round_trip(service))
    service.results.process("harbor")
    asyncio.run(round_trip(service))
    reviewed = reviews.latest("harbor", run.task_id)
    saved_assignment = reviews.assignment(reviewed)
    task = service.workspace.task("harbor", run.task_id)
    service.workspace.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, body="Different intended work")
    )
    with pytest.raises(ApplicationError):
        approve(service, current(service))
    assert reviews.assignment(reviewed) == saved_assignment


def test_conflict_waits_for_answer_then_continues_once(tmp_path, monkeypatch):
    service, _, run, _, reviews = configured(tmp_path)
    change(service.workspace)
    peers(monkeypatch, {"action": "ask_human", "request": "Keep the existing labels?"})
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "waiting"
    questions = Questions(service.workspace)
    question = questions.get("harbor", review.question_id)
    questions.answer(
        "harbor",
        question.id,
        QuestionAnswer(expected_revision=question.revision, answer="Yes, retain those labels."),
    )
    GuidanceReviewer.proposal = {}
    asyncio.run(round_trip(service))
    assert reviews.get("harbor", review.id).status == "decided"
    assert questions.get("harbor", question.id).consumed_answer_revision


def test_mid_review_changes_discard_old_proposal_and_coalesce(tmp_path):
    service, _, run, _, reviews = configured(tmp_path)
    change(service.workspace)
    review = prepare(reviews, "harbor", run.task_id)
    review = reviews.claim("harbor", review.id, review.revision)
    review = reviews.begin_attempt("harbor", review.id)
    review = reviews.started("harbor", review.id, review.revision)
    reviews.save_decision(
        "harbor",
        review.id,
        review.attempt_id,
        {
            "action": "adopt",
            "summary": "Old snapshot",
            "evidence": "Old evidence",
            "body": "This must never be applied.",
        },
    )
    change(service.workspace, "Newer requirement")
    change(service.workspace, "Newest requirement")
    assert prepare(reviews, "harbor", run.task_id) is None
    finished = reviews.finish_attempt(
        "harbor", review.id, review.attempt_id, cleanup_confirmed=True
    )
    assert finished.status == "stale"
    assert service.workspace.task("harbor", run.task_id).body != "This must never be applied."
    newest = prepare(reviews, "harbor", run.task_id)
    assert newest.id != review.id and newest.guidance.documents[0].revision == 4
    assert prepare(reviews, "harbor", run.task_id).id == newest.id


def test_default_add_remove_and_purpose_changes_are_freshness_inputs(tmp_path):
    service, reviews = planning(tmp_path)
    task = planned_task(service)
    doc = Documents(service.workspace).create("harbor", DocumentCreate(title="Style", body="Style"))
    defaults(service.workspace, [{"id": doc.id, "purpose": "reference"}])
    first = prepare(reviews, "harbor", task.id)
    assert any(d.id == doc.id and d.purpose == "reference" for d in first.guidance.documents)
    defaults(service.workspace, [{"id": doc.id, "purpose": "required"}])
    assert reviews.check("harbor", first.id).status == "stale"
    second = prepare(reviews, "harbor", task.id)
    assert any(d.id == doc.id and d.purpose == "required" for d in second.guidance.documents)
    defaults(service.workspace, [])
    assert reviews.check("harbor", second.id).status == "stale"
    with service.workspace.connection() as db:
        assert not pending(service.workspace, db, service.workspace._task(db, "harbor", task.id))


def test_explicit_selection_change_projects_automatic_review_not_manual_repair(tmp_path):
    from flowfield.application import TaskEdit
    from flowfield.browser import BrowserReads

    service, _ = planning(tmp_path)
    task = planned_task(service)
    doc = Documents(service.workspace).create("harbor", DocumentCreate(title="Style", body="Style"))
    service.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, documents=[{"id": doc.id}]),
    )
    assert (
        BrowserReads(service.workspace).task("harbor", task.id).state.label
        == "Guidance review queued"
    )


def test_delivery_rechecks_guidance_after_approval(tmp_path, monkeypatch):
    service, repo, run, _, _ = configured(tmp_path)
    peers(monkeypatch)
    asyncio.run(round_trip(service))
    approved = approve(service, current(service))
    from flowfield.adapters.git_workspace import git

    before = git(repo, "rev-parse", "integration")
    change(service.workspace)
    service.results.process("harbor")
    assert git(repo, "rev-parse", "integration") == before
    assert current(service).status != "delivered"
    asyncio.run(round_trip(service))
    assert not current(service).approved_at
    assert service.results.get("harbor", approved.id).approved_at == approved.approved_at


def test_pause_stop_and_uncertain_cleanup_do_not_apply_or_restart_guidance(tmp_path, monkeypatch):
    from flowfield.execution_models import QueueEdit

    service, _, run, _, reviews = configured(tmp_path)
    change(service.workspace)
    peers(monkeypatch)
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "queued" and not review.attempt_id
    review = reviews.stop("harbor", review.id, review.revision, cleanup_confirmed=True)
    change(service.workspace, "Another edit while stopped")
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    asyncio.run(round_trip(service))
    assert reviews.latest("harbor", run.task_id).id == review.id
    assert not GuidanceReviewer.observed
    reviews.retry("harbor", review.id, review.revision)
    fresh = prepare(reviews, "harbor", run.task_id)
    fresh = reviews.claim("harbor", fresh.id, fresh.revision)
    fresh = reviews.begin_attempt("harbor", fresh.id)
    fresh = reviews.started("harbor", fresh.id, fresh.revision)
    reviews.save_decision(
        "harbor",
        fresh.id,
        fresh.attempt_id,
        {"action": "adopt", "summary": "Aligned", "evidence": "Compared inputs"},
    )
    saved = reviews.finish_attempt("harbor", fresh.id, fresh.attempt_id, cleanup_confirmed=False)
    assert saved.status == "uncertain" and not saved.applied_at
    assert prepare(reviews, "harbor", run.task_id) is None


def test_invalid_proposal_keeps_the_task_and_requires_explicit_retry(tmp_path, monkeypatch):
    service, _, run, _, reviews = configured(tmp_path)
    change(service.workspace)
    before = service.workspace.task("harbor", run.task_id)
    peers(monkeypatch, {"dependencies": [before.key], "body": "Must not be saved"})
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "failed" and not review.applied_at
    task = service.workspace.task("harbor", run.task_id)
    assert task.body == before.body and task.publication == before.publication
    assert task.dependencies == before.dependencies
    asyncio.run(round_trip(service))
    assert len(reviews.attempts("harbor", review.id)["items"]) == 1


def test_preparation_uses_latest_even_with_an_older_selection_revision(tmp_path):
    service, _ = planning(tmp_path)
    changed = change(service.workspace)
    task = service.workspace.create_task(
        "harbor",
        TaskCreate(
            title="New investigation",
            body="Investigate the stated outcome",
            documents=[{"id": "guidance", "revision": 1}],
            preparation=TaskPreparation(completion="report"),
        ),
    )
    assert task.publication.documents[0].revision == changed.revision


def test_reopened_task_reconciles_without_changing_its_delivered_result(tmp_path, monkeypatch):
    from flowfield.application import TaskEdit, TaskPriority, TaskReopen

    service, _, run, _, reviews = configured(tmp_path)
    peers(monkeypatch)
    asyncio.run(round_trip(service))
    approve(service, current(service))
    service.results.process("harbor")
    delivered = current(service)
    assert delivered.status == "delivered"
    task = service.workspace.task("harbor", run.task_id)
    task = service.workspace.reopen_task(
        "harbor", task.id, TaskReopen(expected_revision=task.revision, note="Cover another case")
    )
    task = service.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, preparation=TaskPreparation(completion="code")),
    )
    service.workspace.prioritize_task(
        "harbor", task.id, TaskPriority(expected_revision=task.revision, status="up_next")
    )
    change(service.workspace)
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", task.id)
    assert review.status == "decided" and review.result_id is None
    assert current(service).id == delivered.id
    assert current(service).status == "delivered"
    assert service.workspace.task("harbor", task.id).publication.documents[0].revision == 2


@pytest.mark.parametrize("cycle", [False, True])
def test_reconciliation_updates_existing_subtask_plan_atomically(tmp_path, monkeypatch, cycle):
    from flowfield.application import TaskEdit, TaskPriority
    from flowfield.subtasks import SubtaskCreate, Subtasks

    service, reviews = planning(tmp_path)
    task = service.workspace.create_task(
        "harbor", TaskCreate(title="Research labels", body="Investigate accessible labels")
    )
    plan = Subtasks(service.workspace)
    first = plan.create(
        "harbor", task.id, SubtaskCreate(title="Research", body="Read labels", completion="report")
    )
    second = plan.create(
        "harbor",
        task.id,
        SubtaskCreate(
            title="Summarize", body="Explain findings", completion="report", dependencies=[first.id]
        ),
    )
    task = service.workspace.task("harbor", task.id)
    task = service.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(
            expected_revision=task.revision,
            preparation=TaskPreparation(completion="report"),
        ),
    )
    task = service.workspace.prioritize_task(
        "harbor", task.id, TaskPriority(expected_revision=task.revision, status="up_next")
    )
    change(service.workspace)
    peers(
        monkeypatch,
        {
            "subtasks": [
                {
                    "id": first.key,
                    "body": "Research accessible labels",
                    "dependencies": [second.key] if cycle else [],
                }
            ]
        },
    )
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", task.id)
    assert len(json.loads(review.guidance.task_context)["subtasks"]) == 2
    saved = service.workspace.task("harbor", task.id)
    if cycle:
        assert review.status == "failed"
        assert plan.get("harbor", task.id, first.id) == first
        assert saved.publication == task.publication
    else:
        assert review.status == "decided"
        assert plan.get("harbor", task.id, first.id).body == "Research accessible labels"
        assert saved.agreement_revision == task.agreement_revision + 1
        assert saved.publication.documents[0].revision == 2
