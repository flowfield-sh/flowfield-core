"""Native lifecycle contract using model-free peers, real MCP and real candidate Git."""

import asyncio
import json

import pytest
from test_results import current
from test_supervisor import FakeWorker
from test_task_reviews import configured

from flowfield.adapters.git_workspace import git
from flowfield.errors import ApplicationError
from flowfield.questions import QuestionAnswer, Questions
from flowfield.result_models import ResultReview
from flowfield.worker_settings import occupancy


class Reviewer(FakeWorker):
    decision = "approve"
    observed = []

    async def run(self, model, effort, prompt, tools):
        brief = json.loads(prompt)
        self.observed.append(brief)
        names = {tool["name"] for tool in tools}
        assert "submit_result" not in names and "run_project_checks" not in names
        assert "review" not in names
        assert git(self.cwd, "rev-parse", "HEAD").decode().strip() == brief["candidate_commit"]
        context = json.loads(await self.on_tool("read_context", {"section": "result"}))
        assert len(context["text"]) <= 6000
        action = self.decision if brief["purpose"] == "reviewer" else "answer"
        await self.on_tool(
            "submit_review" if brief["purpose"] == "reviewer" else "answer_review",
            {
                "action": action,
                "summary": "Independent finding",
                "evidence": "Read the exact candidate and agreement.",
                "request": ""
                if action in ("approve", "answer")
                else "Explain the agreed edge case.",
            },
        )
        return {"status": "completed"}


def install(monkeypatch, decision="approve"):
    Reviewer.decision = decision
    Reviewer.observed = []
    monkeypatch.setattr(
        "flowfield.native_review_execution.create",
        lambda choice, directory, cwd, environment, **kw: Reviewer(
            directory, cwd, environment, **kw
        ),
    )


async def round_trip(service):
    await service.review_execution.schedule()
    await asyncio.gather(*list(service.jobs.values()))


def test_native_assessment_does_not_integrate_and_shares_capacity(tmp_path, monkeypatch):
    service, repo, run, _, reviews = configured(tmp_path)
    install(monkeypatch)
    before = git(repo, "rev-parse", "HEAD")
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "decided" and review.verdict.action == "approve"
    assert git(repo, "rev-parse", "HEAD") == before and not current(service).approved_at
    attempts = reviews.attempts("harbor", review.id)["items"]
    assert len(attempts) == 1 and attempts[0]["cleanup_confirmed"]
    assert attempts[0]["applied_agent"]["model"] == "review-fixture"
    assert current(service).independent_review.summary == "Independent finding"
    asyncio.run(round_trip(service))
    assert len(reviews.attempts("harbor", review.id)["items"]) == 1
    result = current(service)
    service.results.review(
        "harbor",
        result.id,
        ResultReview(
            expected_revision=result.revision,
            candidate_commit=result.candidate_commit,
            action="approve",
        ),
    )
    service.results.process("harbor")
    assert current(service).status == "delivered"


def test_stale_question_is_retained_and_cannot_resume_old_review(tmp_path, monkeypatch):
    from flowfield.reviewer_settings import ReviewerEdit

    service, _, run, settings, reviews = configured(tmp_path)
    install(monkeypatch, "ask_human")
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    questions = Questions(service.workspace)
    question = questions.get("harbor", review.question_id)
    questions.answer(
        "harbor",
        question.id,
        QuestionAnswer(expected_revision=question.revision, answer="Retain this evidence."),
    )
    policy = settings.get("harbor", "task")
    settings.configure(
        "harbor", "task", ReviewerEdit(expected_revision=policy.revision, enabled=False)
    )
    asyncio.run(round_trip(service))
    assert reviews.get("harbor", review.id).status == "stale"
    saved = questions.get("harbor", question.id)
    assert saved.status == "withdrawn" and saved.answer == "Retain this evidence."
    assert not saved.delivery.can_edit and not saved.consumed_answer_revision


def test_review_scope_rejects_delivery_and_foreign_identifiers(tmp_path):
    from flowfield.review_tools import ReviewBridge

    service, _, _, _, reviews = configured(tmp_path)
    review = reviews.prepare("harbor", current(service).id)
    review = reviews.claim("harbor", review.id, review.revision)
    review = reviews.begin_attempt("harbor", review.id)
    review = reviews.started("harbor", review.id, review.revision)
    scope = ReviewBridge(reviews, review, review.attempt_id, "reviewer").scope()

    async def check():
        denied = await scope.call("approve_result", {})
        assert denied.isError and "operation_denied" in denied.content[0].text
        foreign = await scope.call("read_context", {"section": "result", "project_id": "foreign"})
        assert foreign.isError and "invalid_request" in foreign.content[0].text
        scope.revoke()
        revoked = await scope.call("read_context", {"section": "result"})
        assert revoked.isError and "scope_closed" in revoked.content[0].text

    asyncio.run(check())


def test_review_api_exposes_bounded_round_diagnostics(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from flowfield.reviewer_api import reviewer_router

    service, _, run, _, reviews = configured(tmp_path)
    install(monkeypatch)
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    app = FastAPI()
    app.include_router(reviewer_router(lambda: service))
    with TestClient(app) as client:
        page = client.get(f"/api/projects/harbor/reviews/{review.id}/attempts")
        assert page.status_code == 200
        attempt = page.json()["items"][0]
        assert attempt["cleanup_confirmed"] and attempt["pid"]
        assert "harness_launch" not in attempt and "session_id" not in attempt
        activity = client.get(
            f"/api/projects/harbor/reviews/{review.id}/attempts/{attempt['id']}/activity"
        )
        assert activity.status_code == 200 and not activity.json()["active"]


def test_changed_policy_cannot_overlap_live_review_or_uncertain_cleanup(tmp_path):
    from flowfield.agent_models import AgentChoice
    from flowfield.reviewer_settings import ReviewerEdit

    service, _, _, settings, reviews = configured(tmp_path)
    result = current(service)
    review = reviews.prepare("harbor", result.id)
    review = reviews.claim("harbor", review.id, review.revision)
    policy = settings.get("harbor", "task")
    settings.configure(
        "harbor",
        "task",
        ReviewerEdit(
            expected_revision=policy.revision,
            enabled=True,
            selection=AgentChoice(model="another-reviewer"),
        ),
    )
    with pytest.raises(ApplicationError, match="still owns"):
        reviews.prepare("harbor", result.id)
    review = reviews.stop("harbor", review.id, review.revision, cleanup_confirmed=False)
    with pytest.raises(ApplicationError, match="still owns"):
        reviews.prepare("harbor", result.id)
    reviews.stop("harbor", review.id, review.revision, cleanup_confirmed=True)
    assert reviews.prepare("harbor", result.id).id != review.id


def test_reprepared_result_waits_for_previous_review_cleanup(tmp_path):
    from flowfield.execution_models import RunAction

    service, _, _, _, reviews = configured(tmp_path)
    original = current(service)
    review = reviews.prepare("harbor", original.id)
    review = reviews.claim("harbor", review.id, review.revision)
    reviews.stop("harbor", review.id, review.revision, cleanup_confirmed=False)
    service.results.reprepare("harbor", original.id, RunAction(expected_revision=original.revision))
    service.results.process("harbor")
    result = current(service)
    assert result.id != original.id and result.next_action.label == "Check reviewer state"
    with pytest.raises(ApplicationError, match="stop it"):
        service.results.review(
            "harbor",
            result.id,
            ResultReview(
                expected_revision=result.revision,
                candidate_commit=result.candidate_commit,
                action="approve",
            ),
        )


def test_clarification_serializes_roles_and_preserves_code(tmp_path, monkeypatch):
    service, _, run, _, reviews = configured(tmp_path)
    install(monkeypatch, "ask_worker")
    original = current(service)
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "queued" and review.recipient == "implementer"
    with service.workspace.connection() as db:
        assert occupancy(db, project="harbor").active == 0
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "queued" and review.recipient == "reviewer"
    assert current(service).source_commit == original.source_commit
    Reviewer.decision = "approve"
    asyncio.run(round_trip(service))
    assert reviews.latest("harbor", run.task_id).verdict.action == "approve"
    assert [brief["purpose"] for brief in Reviewer.observed] == [
        "reviewer",
        "implementer",
        "reviewer",
    ]


def test_human_answer_is_canonical_and_consumed_once(tmp_path, monkeypatch):
    service, _, run, _, reviews = configured(tmp_path)
    install(monkeypatch, "ask_human")
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "waiting"
    questions = Questions(service.workspace)
    question = questions.get("harbor", review.question_id)
    assert question.origin_review_id == review.id and question.delivery.can_edit
    answer = QuestionAnswer(
        expected_revision=question.revision, answer="Use the existing requirement."
    )
    questions.answer("harbor", question.id, answer)
    Reviewer.decision = "approve"
    asyncio.run(round_trip(service))
    saved = questions.get("harbor", question.id)
    assert saved.consumed_answer_revision and saved.status == "applied"
    assert (
        questions.answer("harbor", question.id, answer).consumed_answer_revision
        == saved.consumed_answer_revision
    )
    with pytest.raises(ApplicationError, match="already assigned"):
        questions.answer(
            "harbor",
            question.id,
            QuestionAnswer(expected_revision=saved.revision, answer="Changed scope"),
        )
    assert reviews.latest("harbor", run.task_id).status == "decided"


def test_correction_reuses_result_owner_and_never_integrates(tmp_path, monkeypatch):
    service, repo, run, _, reviews = configured(tmp_path)
    install(monkeypatch, "request_changes")
    before = git(repo, "rev-parse", "HEAD")
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "decided" and review.applied_at
    assert current(service).status == "changes_requested"
    assert service.execution.get("harbor", run.id).status == "changes_requested"
    assert git(repo, "rev-parse", "HEAD") == before


def test_human_result_response_cannot_race_review_owner(tmp_path):
    service, _, _, _, reviews = configured(tmp_path)
    result = current(service)
    review = reviews.prepare("harbor", result.id)
    with pytest.raises(ApplicationError, match="stop it"):
        service.results.review(
            "harbor",
            result.id,
            ResultReview(
                expected_revision=result.revision,
                candidate_commit=result.candidate_commit,
                action="approve",
            ),
        )
    reviews.stop("harbor", review.id, review.revision, cleanup_confirmed=True)
    service.results.review(
        "harbor",
        result.id,
        ResultReview(
            expected_revision=result.revision,
            candidate_commit=result.candidate_commit,
            action="approve",
        ),
    )


def test_uncertain_review_is_not_replayed_after_restart(tmp_path):
    service, _, run, _, reviews = configured(tmp_path)
    review = reviews.prepare("harbor", current(service).id)
    review = reviews.claim("harbor", review.id, review.revision)
    review = reviews.begin_attempt("harbor", review.id)
    data = reviews.attempt(review.attempt_id)
    data.update(native_launch_started=True, cleanup_confirmed=False)
    reviews.save_attempt(review.attempt_id, data)
    reviews.restart()
    review = reviews.latest("harbor", run.task_id)
    stopped = asyncio.run(service.review_execution.stop("harbor", review.id, review.revision))
    assert stopped.status == "uncertain"
    with pytest.raises(ApplicationError):
        reviews.retry("harbor", review.id, stopped.revision)
    confirmed = service.review_execution.confirm_stopped("harbor", review.id, stopped.revision)
    assert confirmed.status == "stopped"
    assert reviews.attempt(confirmed.attempt_id)["cleanup_confirmed_by"] == "human"


def test_upgrade_keeps_old_candidates_unscheduled(tmp_path, monkeypatch):
    from flowfield import migrations, storage
    from flowfield.application import Workspace
    from flowfield.supervisor import Supervisor

    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 74)
        )
        service, _, run, _, _ = configured(tmp_path)
        before = current(service)
        queue = service.execution.settings("harbor")
    upgraded = Supervisor(Workspace(service.workspace.directory))
    install(monkeypatch)
    asyncio.run(round_trip(upgraded))
    assert upgraded.review_execution.reviews.latest("harbor", run.task_id) is None
    assert current(upgraded) == before and not before.review_requested
    assert upgraded.execution.settings("harbor") == queue
    assert storage.backups(upgraded.workspace.directory)[0]["schema_version"] == 74


def test_review_migration_rolls_back_and_recovers(tmp_path, monkeypatch):
    from flowfield import migrations, storage
    from flowfield.application import Workspace
    from flowfield.migrations import Migration
    from flowfield.migrations.v075_native_reviews import apply

    old_chain = tuple(m for m in migrations.MIGRATIONS if m.version <= 74)
    with monkeypatch.context() as old:
        old.setattr(migrations, "MIGRATIONS", old_chain)
        service, _, _, _, _ = configured(tmp_path)

    def interrupted(db):
        apply(db)
        raise RuntimeError("Interrupted native review migration")

    with monkeypatch.context() as broken:
        broken.setattr(migrations, "MIGRATIONS", (*old_chain, Migration(75, interrupted)))
        with pytest.raises(Exception, match="Interrupted native review migration"):
            Workspace(service.workspace.directory)
    with storage.connect(service.workspace.directory / storage.DATABASE) as db:
        assert storage.version(db) == 74
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='review_attempts'").fetchone()
    upgraded = Workspace(service.workspace.directory)
    with upgraded.connection() as db:
        assert storage.version(db) == migrations.current_version()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_policy_change_stales_old_verdict_and_schedules_fresh_assessment(tmp_path, monkeypatch):
    from flowfield.agent_models import AgentChoice
    from flowfield.reviewer_settings import ReviewerEdit

    service, _, run, settings, reviews = configured(tmp_path)
    install(monkeypatch)
    asyncio.run(round_trip(service))
    original = reviews.latest("harbor", run.task_id)
    policy = settings.get("harbor", "task")
    settings.configure(
        "harbor",
        "task",
        ReviewerEdit(
            expected_revision=policy.revision,
            enabled=True,
            selection=AgentChoice(model="changed-reviewer"),
        ),
    )
    asyncio.run(round_trip(service))
    latest = reviews.latest("harbor", run.task_id)
    assert latest.id != original.id and latest.status == "decided"
    assert reviews.get("harbor", original.id).status == "stale"
    assert not current(service).approved_at


def test_stop_during_preparation_waits_for_owned_git_work(tmp_path, monkeypatch):
    import threading

    from flowfield.adapters.local_execution import LocalHost

    service, _, run, _, reviews = configured(tmp_path)
    install(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = LocalHost.prepare

    def delayed(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(LocalHost, "prepare", delayed)

    async def exercise():
        await service.review_execution.schedule()
        while not entered.is_set():
            await asyncio.sleep(0.01)
        review = reviews.latest("harbor", run.task_id)
        stop = asyncio.create_task(
            service.review_execution.stop("harbor", review.id, review.revision)
        )
        await asyncio.sleep(0.02)
        assert not stop.done()
        with service.workspace.connection() as db:
            assert occupancy(db, project="harbor").uncertain == 1
        release.set()
        saved = await stop
        assert saved.status == "stopped"
        assert reviews.attempt(saved.attempt_id)["checkout"]
        assert not Reviewer.observed

    asyncio.run(exercise())


def test_missing_setup_failure_is_retained_and_retry_is_explicit(tmp_path, monkeypatch):
    service, _, run, _, reviews = configured(tmp_path)

    def missing(*args, **kwargs):
        raise ApplicationError("harness_missing", "Install the configured reviewer harness.", 409)

    monkeypatch.setattr("flowfield.native_review_execution.create", missing)
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "failed" and "Install" in review.problem
    asyncio.run(round_trip(service))
    assert len(reviews.attempts("harbor", review.id)["items"]) == 1
    install(monkeypatch)
    reviews.retry("harbor", review.id, review.revision)
    asyncio.run(round_trip(service))
    assert reviews.latest("harbor", run.task_id).status == "decided"


def test_review_cannot_modify_candidate_or_call_coordinator_tools(tmp_path, monkeypatch):
    service, repo, run, _, reviews = configured(tmp_path)

    class EditingReviewer(Reviewer):
        async def run(self, *args):
            (self.cwd / "result.txt").write_text("unauthorized edit")
            return await super().run(*args)

    monkeypatch.setattr(
        "flowfield.native_review_execution.create",
        lambda choice, directory, cwd, environment, **kw: EditingReviewer(
            directory, cwd, environment, **kw
        ),
    )
    Reviewer.decision = "approve"
    before = git(repo, "rev-parse", "HEAD")
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == "failed" and review.verdict is None
    assert "changed its inspection checkout" in review.problem
    assert git(repo, "rev-parse", "HEAD") == before
