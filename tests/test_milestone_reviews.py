"""Whole-outcome native scheduling and corrections against actual Git, without live models."""

import asyncio
from pathlib import Path

import pytest
from delegated_integration_fixtures import assess, policy, prepared
from fake_worker import FakeWorker
from milestone_reviews_fixtures import OutcomeReviewer, fixture, install, latest, step

from flowfield.adapters.git_workspace import git
from flowfield.application import MilestoneEdit
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit
from flowfield.milestone_promotion import MilestonePromotions
from flowfield.milestone_review_status import status
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.milestones import Milestones
from flowfield.questions import QuestionAnswer, Questions
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.supervisor import Supervisor
from flowfield.task_reviews import TaskReviews
from flowfield.verification_models import VerificationReview
from flowfield.verification_plans import VerificationPlanEdit, VerificationPlans
from flowfield.worker_settings import occupancy


def test_combined_review_reaches_human_promotion_without_a_verifier_task(tmp_path, monkeypatch):
    service, repo, base, milestone, _, _ = fixture(tmp_path)
    install(monkeypatch)
    asyncio.run(step(service))
    value = latest(service, milestone)
    assert value.status == "ready", value.problem
    assert value.report.coverage == "complete" and value.promotion.candidate_commit
    assert git(repo, "rev-parse", "main").decode().strip() == base
    with service.workspace.connection() as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert occupancy(db, project="harbor").active == 0
    asyncio.run(step(service))
    assert OutcomeReviewer.calls == 1
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    assert status(service.workspace, "harbor", milestone.id).message == (
        "The combined outcome is ready for your review."
    )
    records = MilestoneVerifications(service.workspace)
    records.review(
        "harbor", value.id, VerificationReview(expected_revision=value.revision, action="accept")
    )
    MilestonePromotions(service.workspace).process("harbor")
    assert records.get("harbor", value.id).status == "accepted"
    assert git(repo, "rev-parse", "main").decode().strip() == value.promotion.candidate_commit


def test_human_question_releases_capacity_and_resumes_saved_owner_once(tmp_path, monkeypatch):
    service, _, _, milestone, _, _ = fixture(tmp_path)
    install(monkeypatch, "question")
    asyncio.run(step(service))
    value = latest(service, milestone)
    reviews = service.milestone_review_execution.reviews
    review = reviews.get("harbor", value.review_id)
    assert review.status == "waiting"
    questions = Questions(service.workspace)
    question = questions.get("harbor", review.question_id)
    assert question.verification_url.endswith(f"verification={value.id}")
    assert question.delivery.can_edit
    with service.workspace.connection() as db:
        assert occupancy(db, project="harbor").active == 0
        from flowfield.attention import workspace_attention_requests

        assert milestone.key in workspace_attention_requests(db, "harbor")
    questions.answer(
        "harbor",
        question.id,
        QuestionAnswer(
            expected_revision=question.revision,
            answer="The saved requirement already defines the boundary.",
        ),
    )
    install(monkeypatch)
    asyncio.run(step(service))
    assert latest(service, milestone).status == "ready"
    assert questions.get("harbor", question.id).status == "applied"
    assert len(reviews.attempts("harbor", review.id)["items"]) == 2


@pytest.mark.parametrize("action", ["correct", "recover_correction"])
def test_failed_check_schedules_existing_contribution_correction_without_rewriting_history(
    tmp_path, monkeypatch, action
):
    service, _, _, milestone, delivery, result = fixture(tmp_path)
    install(monkeypatch, action)
    original = service.workspace.task("harbor", result.task_id)
    asyncio.run(step(service))
    value = latest(service, milestone)
    assert value.status == "failed" and value.report.findings[0].verdict == "failed"
    task = service.workspace.task("harbor", result.task_id)
    assert task.status == "up_next" and task.agreement_revision == original.agreement_revision
    assert service.results.get("harbor", result.id).status == "delivered"
    run = service.execution.get("harbor", result.run_id)
    assert run.status == "changes_requested" and run.next_correction
    assert (
        run.next_correction.base_commit
        == git(Path(delivery.path), "rev-parse", "HEAD").decode().strip()
    )
    asyncio.run(step(service))
    assert OutcomeReviewer.calls == 1


def test_changed_criteria_withdraws_question_and_preserves_answer(tmp_path, monkeypatch):
    service, _, _, milestone, _, _ = fixture(tmp_path)
    install(monkeypatch, "question")
    asyncio.run(step(service))
    value = latest(service, milestone)
    reviews = service.milestone_review_execution.reviews
    review = reviews.get("harbor", value.review_id)
    questions = Questions(service.workspace)
    question = questions.get("harbor", review.question_id)
    questions.answer(
        "harbor",
        question.id,
        QuestionAnswer(expected_revision=question.revision, answer="Preserve this answer."),
    )
    Milestones(service.workspace).edit(
        "harbor",
        milestone.id,
        MilestoneEdit(expected_revision=1, acceptance_criteria="Changed requirement."),
    )
    assert reviews.check("harbor", review.id).status == "stale"
    retained = questions.get("harbor", question.id)
    assert retained.status == "withdrawn" and retained.answer == "Preserve this answer."
    assert latest(service, milestone).status == "stale"


@pytest.mark.parametrize("change", ["plan", "reviewer", "source", "destination"])
def test_changed_verification_inputs_cannot_accept_a_passing_native_report(
    tmp_path, monkeypatch, change
):
    service, repo, _, milestone, delivery, _ = fixture(tmp_path)
    install(monkeypatch)
    asyncio.run(step(service))
    value = latest(service, milestone)
    assert value.status == "ready"
    if change == "plan":
        plans = VerificationPlans(service.workspace)
        plans.save(
            "harbor",
            milestone.id,
            VerificationPlanEdit(
                expected_revision=plans.get("harbor", milestone.id).revision,
                milestone_revision=milestone.revision,
                checks=["Revised whole-outcome check"],
                instructions="Inspect the updated procedure.",
            ),
        )
    elif change == "reviewer":
        Reviewers(service.workspace).configure(
            "harbor", "milestone", ReviewerEdit(expected_revision=2, enabled=False)
        )
    else:
        branch = delivery.branch if change == "source" else delivery.destination_branch
        head = git(repo, "rev-parse", branch).decode().strip()
        moved = (
            git(
                repo,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit-tree",
                f"{head}^{{tree}}",
                "-p",
                head,
                "-m",
                "External branch advance",
            )
            .decode()
            .strip()
        )
        git(repo, "update-ref", f"refs/heads/{branch}", moved, head)
    with pytest.raises(ApplicationError):
        MilestoneVerifications(service.workspace).review(
            "harbor",
            value.id,
            VerificationReview(expected_revision=value.revision, action="accept"),
        )
    review = service.milestone_review_execution.reviews.check("harbor", value.review_id)
    assert review.status == "stale"
    retained = MilestoneVerifications(service.workspace).get("harbor", value.id)
    assert retained.report == value.report and not retained.reviewed_at


def test_pause_restart_and_uncertain_cleanup_share_worker_capacity(tmp_path):
    service, _, _, milestone, _, _ = fixture(tmp_path)
    reviews = service.milestone_review_execution.reviews
    value = reviews.prepare("harbor", milestone.id)
    MilestonePromotions(service.workspace).process("harbor")
    review = reviews.enqueue("harbor", value.id)
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    with pytest.raises(ApplicationError, match="queue"):
        reviews.claim("harbor", review.id, review.revision)
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    reviews.claim("harbor", review.id, review.revision)
    reviews.restart()
    review = reviews.get("harbor", review.id)
    assert review.status == "uncertain"
    with service.workspace.connection() as db:
        assert occupancy(db, project="harbor").uncertain == 1
    stopped = service.milestone_review_execution.confirm_stopped(
        "harbor", review.id, review.revision
    )
    assert stopped.status == "stopped"
    assert reviews.retry("harbor", review.id, stopped.revision).status == "queued"


def dependent_contribution(service, repo, milestone, prerequisite):
    import os

    from project_fixtures import task_request

    from flowfield.adapters.local_execution import LocalHost
    from flowfield.application import TaskPublish
    from flowfield.execution_models import WorkerResult

    store = service.workspace
    task = store.create_task(
        "harbor",
        task_request(
            title="Usage",
            body="Document the feature",
            milestone_id=milestone.id,
            dependencies=[store.task("harbor", prerequisite).key],
            status="up_next",
        ),
    )
    store.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            expected_revision=task.revision,
            completion="code",
        ),
    )
    head = service.integrations.head("harbor", task.id)
    branch = store.task("harbor", task.id).publication.target_branch
    run = service.execution.claim(
        "harbor",
        head,
        {head: service.integrations.available("harbor", head, branch)},
        task_id=task.id,
    )
    service.execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(store.directory, repo, run.id, head)
    (env.checkout / "USAGE.md").write_text("Use the combined feature.\n")
    commit, _ = env.snapshot(head)
    service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary="Usage documented", checks="true"),
        commit=commit,
    )
    service.results.process("harbor")
    result = service.results.page("harbor", task.id).items[0]
    review = assess(store, result)
    TaskReviews(store).delegate("harbor", review.id)
    service.results.process("harbor")
    assert service.results.get("harbor", result.id).status == "delivered"
    return store.task("harbor", task.id)


def test_correction_returns_through_task_review_and_fresh_whole_outcome(tmp_path, monkeypatch):
    import os

    from review_execution_fixtures import install as install_task
    from review_execution_fixtures import round_trip

    from flowfield.adapters.local_execution import LocalHost
    from flowfield.execution_models import WorkerResult

    service, repo, _, milestone, _, result = fixture(tmp_path)
    dependent = dependent_contribution(service, repo, milestone, result.task_id)
    install(monkeypatch, "correct")
    asyncio.run(step(service))
    failed = latest(service, milestone)
    assert service.workspace.task("harbor", dependent.id).reconciliation_reason
    head = service.integrations.head("harbor", result.task_id)
    available = service.integrations.available("harbor", head)
    run = service.execution.claim("harbor", head, {head: available}, task_id=result.task_id)
    assert run and run.correction
    service.execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(service.workspace.directory, repo, run.id, head)
    (env.checkout / "feature.txt").write_text("Corrected combined feature\n")
    commit, _ = env.snapshot(head)
    service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary="Corrected feature", checks="true"),
        commit=commit,
    )
    service.results.process("harbor")
    install_task(monkeypatch)
    asyncio.run(round_trip(service))
    asyncio.run(round_trip(service))
    service.results.process("harbor")
    assert service.results.page("harbor", result.task_id).items[0].status == "delivered"
    install(monkeypatch)
    asyncio.run(step(service))
    fresh = latest(service, milestone)
    assert fresh.id != failed.id and fresh.status == "ready", fresh.problem
    assert fresh.promotion.candidate_commit != failed.promotion.candidate_commit
    resolved = service.workspace.task("harbor", dependent.id)
    assert not resolved.reconciliation_reason and resolved.readiness == "ready"
    assert resolved.publication == dependent.publication  # No source/intent adoption.
    assert resolved.agreement_revision == dependent.agreement_revision
    assert fresh.dependency_resolutions[0].task_id == dependent.id
    assert next(t for t in fresh.tasks if t.task_id == dependent.id).reconciliation_reason
    assert any(v.reconciliation_reason for v in resolved.revisions)
    assert len(service.execution.page("harbor").items) == 3  # No extra documentation worker.
    from flowfield.milestone_verification import current_problem

    with service.workspace.connection() as db:
        assert current_problem(db, fresh) is None
    asyncio.run(step(service))
    assert latest(service, milestone).id == fresh.id  # No self-invalidating review loop.
    from flowfield.application import TaskEdit

    service.workspace.edit_task(
        "harbor", dependent.id, TaskEdit(expected_revision=resolved.revision, body="Changed intent")
    )
    with service.workspace.connection() as db:
        assert current_problem(db, fresh)  # The receipt grants no future revision exemption.
    assert (
        MilestoneVerifications(service.workspace)
        .get("harbor", failed.id)
        .report.findings[0]
        .verdict
        == "failed"
    )


@pytest.mark.parametrize("inherit", [False, True])
def test_missing_plan_creates_one_retained_question_and_does_not_guess_checks(tmp_path, inherit):
    store, _, _, milestone, delivery, results, result = prepared(tmp_path)
    if inherit:
        from delegated_integration_fixtures import workspace_policy

        workspace_policy(store, True)
    else:
        policy(store, milestone, delivery, True)
    review = assess(store, result)
    TaskReviews(store).delegate("harbor", review.id)
    results.process("harbor")
    service = Supervisor(store)
    reviewers = Reviewers(store)
    reviewers.configure("harbor", "milestone", ReviewerEdit(expected_revision=1, enabled=False))
    asyncio.run(step(service))
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM questions").fetchone()[0] == 0
    reviewers.configure("harbor", "milestone", ReviewerEdit(expected_revision=2, enabled=True))
    asyncio.run(step(service))
    asyncio.run(step(service))
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM milestone_verifications").fetchone()[0] == 0
        rows = db.execute("SELECT id FROM questions").fetchall()
        assert len(rows) == 1
        from flowfield.attention import workspace_attention_requests

        assert milestone.key in workspace_attention_requests(db, "harbor")
    question = Questions(store).get("harbor", rows[0][0])
    assert "verification plan" in question.question
    VerificationPlans(store).save(
        "harbor",
        milestone.id,
        VerificationPlanEdit(
            expected_revision=1,
            milestone_revision=1,
            checks=["Feature works"],
            instructions="Inspect the combined feature.",
        ),
    )
    assert Questions(store).get("harbor", question.id).status == "withdrawn"


@pytest.mark.parametrize("reader", ["milestone", "sibling"])
def test_review_reads_release_a_pending_writer(tmp_path, monkeypatch, reader):
    import sqlite3
    import time
    from threading import Event, Thread

    from flowfield.milestone_review_status import status

    if reader == "milestone":
        service, _, _, milestone, _, _ = fixture(tmp_path)
        store = service.workspace
    else:
        store, _, _, milestone, delivery, _, result = prepared(tmp_path)
        policy(store, milestone, delivery, True)
    waiting = Event()
    errors = []

    def write():
        try:
            with sqlite3.connect(store.database, timeout=2) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE projects SET description='Concurrent update' WHERE id='harbor'")
                waiting.set()
                db.commit()
        except Exception as error:
            errors.append(error)

    writer = Thread(target=write)

    def pending():
        writer.start()
        assert waiting.wait(1)
        deadline = time.monotonic() + 1
        while True:
            probe = sqlite3.connect(store.database, timeout=0)
            try:
                probe.execute("SELECT name FROM projects").fetchall()
            except sqlite3.OperationalError as error:
                assert "locked" in str(error)
                break
            finally:
                probe.close()
            assert time.monotonic() < deadline
            time.sleep(0.001)

    if reader == "milestone":
        original = Reviewers.resolve

        def resolve(owner, db, *args, **kwargs):
            value = original(owner, db, *args, **kwargs)
            pending()
            return value

        monkeypatch.setattr(Reviewers, "resolve", resolve)
    else:
        from flowfield import task_reviews

        original_settings = task_reviews.read_project

        def settings(db, project):
            value = original_settings(db, project)
            pending()
            return value

        monkeypatch.setattr(task_reviews, "read_project", settings)
    try:
        if reader == "milestone":
            assert status(store, "harbor", milestone.id).plan
        else:
            assert not TaskReviews(store).refresh_destination("harbor", result.id)
    finally:
        writer.join(3)
    assert not writer.is_alive() and not errors
    assert store.project("harbor").description == "Concurrent update"


def test_service_close_stops_milestone_review_even_after_scheduler_failure(tmp_path, monkeypatch):
    service, _, _, milestone, _, _ = fixture(tmp_path)
    reviews = service.milestone_review_execution.reviews

    async def exercise():
        entered = asyncio.Event()

        class WaitingReviewer(FakeWorker):
            async def prompt(self, text, on_permission, *, attachments=None):
                entered.set()
                await asyncio.Event().wait()

        monkeypatch.setattr(
            "flowfield.native_review_execution.create",
            lambda choice, directory, cwd, env=None, **kwargs: WaitingReviewer(
                directory, cwd, env, **kwargs
            ),
        )
        value = reviews.prepare("harbor", milestone.id)
        await asyncio.to_thread(MilestonePromotions(service.workspace).process, "harbor")
        review = reviews.enqueue("harbor", value.id)
        await service.milestone_review_execution.start_queued("harbor", review.id)
        await asyncio.wait_for(entered.wait(), 5)

        async def failed_scheduler():
            raise RuntimeError("Recorded scheduler failure")

        service.loop_task = asyncio.create_task(failed_scheduler())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="Recorded scheduler failure"):
            await asyncio.wait_for(service.close(), 5)
        assert not service.jobs and not service.clients
        assert reviews.get("harbor", review.id).status == "stopped"
        with service.workspace.connection() as db:
            assert occupancy(db, project="harbor").active == 0

    asyncio.run(exercise())


def test_scheduling_retires_stale_waiting_question_without_a_main_chat_turn(tmp_path, monkeypatch):
    service, _, _, milestone, _, _ = fixture(tmp_path)
    install(monkeypatch, "question")
    asyncio.run(step(service))
    value = latest(service, milestone)
    reviews = service.milestone_review_execution.reviews
    review = reviews.get("harbor", value.review_id)
    Milestones(service.workspace).edit(
        "harbor",
        milestone.id,
        MilestoneEdit(expected_revision=1, acceptance_criteria="Changed requirement."),
    )
    asyncio.run(step(service))
    assert reviews.get("harbor", review.id).status == "stale"
    assert Questions(service.workspace).get("harbor", review.question_id).status == "withdrawn"


def test_failed_execution_is_actionable_and_retry_has_one_owner(tmp_path, monkeypatch):
    from flowfield.attention import attention_page

    service, _, _, milestone, _, _ = fixture(tmp_path)

    def missing(*args, **kwargs):
        raise ApplicationError("harness_missing", "Install the milestone reviewer harness.", 409)

    monkeypatch.setattr("flowfield.native_review_execution.create", missing)
    asyncio.run(step(service))
    value = latest(service, milestone)
    reviews = service.milestone_review_execution.reviews
    review = reviews.get("harbor", value.review_id)
    assert value.status == "failed" and review.status == "failed"
    assert any(
        item.id == value.id
        for item in attention_page(service.workspace, None, "action", workspace_id="harbor").items
    )
    install(monkeypatch)
    reviews.retry("harbor", review.id, review.revision)
    asyncio.run(step(service))
    assert latest(service, milestone).status == "ready"


def test_milestone_review_scope_cannot_mutate_intent_or_other_contributions(tmp_path):
    service, _, _, milestone, _, _ = fixture(tmp_path)
    owner = service.milestone_review_execution
    value = owner.reviews.prepare("harbor", milestone.id)
    MilestonePromotions(service.workspace).process("harbor")
    review = owner.reviews.enqueue("harbor", value.id)
    review = owner.reviews.claim("harbor", review.id, review.revision)
    review = owner.reviews.begin_attempt("harbor", review.id)
    review = owner.reviews.started("harbor", review.id, review.revision)
    launch = owner.launch(review, review.attempt_id)

    async def check():
        denied = await launch.scope.call("edit_milestone", {})
        assert denied.isError
        denied = await launch.scope.call("read_context", {"section": "contribution:foreign"})
        assert denied.isError
        denied = await launch.scope.call(
            "submit_milestone_review",
            {
                "action": "report",
                "summary": "No evidence",
                "evidence": "No access",
                "report": {
                    "verification_id": "foreign",
                    "coverage": "complete",
                    "coverage_notes": "All",
                    "findings": [{"check": 1, "verdict": "passed", "evidence": "Claim"}],
                },
            },
        )
        assert denied.isError

    asyncio.run(check())
