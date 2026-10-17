"""Review policy and ownership against real candidates; no model calls or delegated delivery."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from execution_fixtures import fixture as execution_fixture
from pydantic import ValidationError
from result_fixtures import current, fixture
from task_reviews_fixtures import configured, verdict

from flowfield.adapters.git_workspace import git
from flowfield.agent_models import AgentChoice
from flowfield.application import MilestoneCreate, TaskEdit, Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, RunAction
from flowfield.milestones import Milestones
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.task_reviews import ReviewVerdict, TaskReviews
from flowfield.worker_settings import occupancy


def test_defaults_inheritance_disable_and_reset_are_independent(tmp_path):
    # Policy inheritance needs a task identity, not a Git candidate and delivery checks.
    workspace = execution_fixture(tmp_path, count=1).workspace
    task_id = workspace.tasks("harbor")[0].id
    reviewers = Reviewers(workspace)
    reviewers.configure(
        "harbor",
        "task",
        ReviewerEdit(
            expected_revision=1, enabled=True, selection=AgentChoice(model="review-fixture")
        ),
    )
    scope = {"project": "harbor", "entity": task_id}
    task = reviewers.get("harbor", "task", **scope)
    assert task.enabled is None and task.effective.enabled
    assert task.effective.agent.choice.model == "review-fixture"
    assert task.effective.agent.source == "workspace"
    assert reviewers.get("harbor", "milestone").effective.enabled
    assert reviewers.get("harbor", "milestone").effective.agent is None
    disabled = reviewers.configure(
        "harbor",
        "task",
        ReviewerEdit(
            expected_revision=task.revision,
            enabled=False,
            selection=AgentChoice(model="special-review"),
        ),
        **scope,
    )
    assert not disabled.effective.enabled
    assert disabled.effective.agent.source == "override"
    defaults = reviewers.get("harbor", "task")
    reviewers.configure(
        "harbor",
        "task",
        ReviewerEdit(
            expected_revision=defaults.revision,
            enabled=True,
            selection=AgentChoice(model="new-default"),
        ),
    )
    assert reviewers.get("harbor", "task", **scope).effective.agent.choice.model == "special-review"
    reset = reviewers.configure(
        "harbor", "task", ReviewerEdit(expected_revision=disabled.revision), **scope
    )
    assert reset.effective.enabled and reset.effective.agent.choice.model == "new-default"
    with pytest.raises(ApplicationError):
        reviewers.configure(
            "harbor", "task", ReviewerEdit(expected_revision=disabled.revision), **scope
        )
    milestone = Milestones(workspace).create("harbor", MilestoneCreate(title="Combined outcome"))
    reviewers.configure(
        "harbor", "milestone", ReviewerEdit(expected_revision=1, enabled=False), entity=milestone.id
    )
    assert reviewers.get("harbor", "task", **scope).effective.enabled
    with pytest.raises(ApplicationError, match="outside"):
        reviewers.get("harbor", "task", project="other", entity=task_id)
    with pytest.raises(ApplicationError, match="requires a task"):
        reviewers.get("harbor", "task", project="harbor")


def test_enabled_without_model_never_guesses_or_starts_review(tmp_path):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    reviews = TaskReviews(service.workspace)
    with pytest.raises(ApplicationError, match="Choose a workspace or task reviewer"):
        reviews.prepare("harbor", current(service).id)
    with service.workspace.connection() as db:
        assert db.execute("SELECT count(*) FROM task_reviews").fetchone()[0] == 0
    assert current(service).approved_at is None


def test_standalone_review_is_idempotent_and_grants_no_delivery(tmp_path):
    service, repo, run, _, reviews = configured(tmp_path)
    result = current(service)
    before = git(repo, "rev-parse", "HEAD")
    task_before = service.workspace.task("harbor", run.task_id)
    review = reviews.prepare("harbor", result.id)
    # A review saved before file support has no added report fields in its JSON.
    with service.workspace.connection(write=True) as db:
        saved = review.model_dump()
        for key in ("completion", "output_ids", "outcome"):
            saved["binding"].pop(key)
        db.execute("UPDATE task_reviews SET data=? WHERE id=?", (json.dumps(saved), review.id))
    assert review.binding.milestone_id is None
    assert reviews.prepare("harbor", result.id) == review
    assert "Deliver the result" in reviews.context("harbor", review.id, "assignment")
    assert "Worker-reported checks" in reviews.context("harbor", review.id, "result")
    assert "test -f result.txt" in reviews.context("harbor", review.id, "validation")
    prepared = reviews.claim("harbor", review.id, review.revision)
    assert prepared.status == "preparing" and prepared.started_at is None
    running = reviews.started("harbor", prepared.id, prepared.revision)
    with service.workspace.connection() as db:
        assert occupancy(db, project="harbor").active == 1
    decided = reviews.conclude(
        "harbor", review.id, running.revision, verdict(), cleanup_confirmed=True
    )
    assert decided.status == "decided"
    assert (
        reviews.conclude("harbor", review.id, running.revision, verdict(), cleanup_confirmed=True)
        == decided
    )
    service.results.process("harbor")
    presented = current(service)
    assert presented.model_dump(exclude={"independent_review"}) == result.model_dump(
        exclude={"independent_review"}
    )
    assert presented.independent_review.summary == verdict().summary
    assert presented.approved_at is None
    assert service.workspace.task("harbor", run.task_id) == task_before
    assert git(repo, "rev-parse", "HEAD") == before
    with service.workspace.connection() as db:
        assert occupancy(db, project="harbor").active == 0
    with pytest.raises(ApplicationError):
        reviews.conclude(
            "harbor",
            review.id,
            decided.revision,
            verdict("request_changes"),
            cleanup_confirmed=True,
        )


@pytest.mark.parametrize("change", ["policy", "disabled", "target", "settings", "result", "intent"])
def test_changed_bindings_cannot_produce_an_approval(tmp_path, change):
    service, repo, run, reviewers, reviews = configured(tmp_path)
    result = current(service)
    queued = reviews.prepare("harbor", result.id)
    prepared = reviews.claim("harbor", queued.id, queued.revision)
    running = reviews.started("harbor", prepared.id, prepared.revision)
    if change in ("policy", "disabled"):
        saved = reviewers.get("harbor", "task")
        reviewers.configure(
            "harbor",
            "task",
            ReviewerEdit(
                expected_revision=saved.revision,
                enabled=change != "disabled",
                selection=AgentChoice(model="different-reviewer"),
            ),
        )
    elif change == "target":
        git(
            repo,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "Moved target",
        )
    elif change == "settings":
        from flowfield.integration_models import IntegrationConfig

        settings = service.integrations.settings("harbor")
        service.integrations.configure(
            "harbor",
            IntegrationConfig(
                expected_revision=settings.revision,
                target_branch=settings.target_branch,
                checks=["test -f README.md"],
            ),
        )
    elif change == "result":
        service.results.reprepare("harbor", result.id, RunAction(expected_revision=result.revision))
    else:
        task = service.workspace.task("harbor", run.task_id)
        service.workspace.edit_task(
            "harbor", task.id, TaskEdit(expected_revision=task.revision, body="Changed agreement")
        )
    stale = reviews.conclude(
        "harbor", queued.id, running.revision, verdict(), cleanup_confirmed=True
    )
    assert stale.status == "stale" and stale.verdict is None
    assert not current(service).approved_at


def test_concurrent_claims_pause_and_unknown_cleanup_preserve_ownership(tmp_path):
    service, _, _, _, reviews = configured(tmp_path)
    queued = reviews.prepare("harbor", current(service).id)
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    with pytest.raises(ApplicationError, match="queue"):
        reviews.claim("harbor", queued.id, queued.revision)
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))

    def claim():
        try:
            return reviews.claim("harbor", queued.id, queued.revision)
        except ApplicationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: claim(), range(2)))
    assert sum(value is not None for value in values) == 1
    prepared = next(value for value in values if value)
    running = reviews.started("harbor", prepared.id, prepared.revision)
    uncertain = reviews.conclude(
        "harbor", queued.id, running.revision, verdict(), cleanup_confirmed=False
    )
    assert uncertain.status == "uncertain" and uncertain.verdict is None
    with service.workspace.connection() as db:
        assert occupancy(db, project="harbor").uncertain == 1
    stopped = reviews.stop("harbor", queued.id, uncertain.revision, cleanup_confirmed=True)
    assert stopped.status == "stopped"
    with service.workspace.connection() as db:
        assert occupancy(db, project="harbor").uncertain == 0


def test_restart_retains_uncertain_slot_and_frozen_reviewer(tmp_path):
    service, _, _, _, reviews = configured(tmp_path)
    queued = reviews.prepare("harbor", current(service).id)
    prepared = reviews.claim("harbor", queued.id, queued.revision)
    running = reviews.started("harbor", prepared.id, prepared.revision)
    reopened = TaskReviews(Workspace(service.workspace.directory))
    reopened.restart()
    saved = reopened.get("harbor", queued.id)
    assert saved.status == "uncertain" and saved.binding == running.binding
    assert saved.verdict is None
    with service.workspace.connection() as db:
        assert occupancy(db, project="harbor").uncertain == 1
    reopened.restart()
    assert reopened.get("harbor", queued.id) == saved


def test_review_and_worker_share_capacity(tmp_path):
    from project_fixtures import task_request

    from flowfield.adapters.git_workspace import baseline
    from flowfield.application import TaskPublish

    service, repo, _, _, reviews = configured(tmp_path)
    task = service.workspace.create_task(
        "harbor",
        task_request(
            id="another",
            title="Independent work",
            body="Keep this separate",
            status="up_next",
        ),
    )
    service.workspace.publish_task(
        "harbor", task.id, TaskPublish(expected_revision=task.revision, completion="code")
    )
    queued = reviews.prepare("harbor", current(service).id)
    prepared = reviews.claim("harbor", queued.id, queued.revision)
    head = baseline(repo)
    assert service.execution.claim("harbor", head, {head: set()}, task_id=task.id) is None
    # Even interrupted startup keeps the slot reserved until cleanup is established.
    reviews.restart()
    uncertain = reviews.get("harbor", prepared.id)
    assert uncertain.status == "uncertain"
    assert service.execution.claim("harbor", head, {head: set()}, task_id=task.id) is None
    reviews.stop("harbor", prepared.id, uncertain.revision, cleanup_confirmed=True)
    worker = service.execution.claim("harbor", head, {head: set()}, task_id=task.id)
    assert worker and worker.task_id == task.id


@pytest.mark.parametrize("action", ["request_changes", "ask_worker", "ask_human"])
def test_structured_recipient_requires_a_concrete_request(action):
    with pytest.raises(ValidationError):
        ReviewVerdict(action=action, summary="Needs input", evidence="Unclear requirement")
    assert verdict(action).request
    with pytest.raises(ValidationError):
        ReviewVerdict(action="approve", summary="Okay", evidence="Checked", request="But fix this")
