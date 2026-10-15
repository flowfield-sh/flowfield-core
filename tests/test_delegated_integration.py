"""Human policy, exact review and Git recovery use the existing delivery owner."""

import os
from pathlib import Path

import pytest
from project_fixtures import fixture_stage_change, task_request
from test_milestone_delivery import setup
from test_task_reviews import verdict

from flowfield.adapters.git_workspace import git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import TaskPublish
from flowfield.delivery_models import TaskIntegrationPolicyEdit
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction, WorkerResult
from flowfield.milestone_delivery import MilestoneDeliveries
from flowfield.results import Results
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.task_reviews import TaskReviews


def prepared(tmp_path):
    execution, integrations, repo, base, milestone, delivery = setup(tmp_path)
    store = execution.workspace
    task = store.create_task(
        "harbor",
        task_request(
            title="Contribution", body="Add a file", milestone_id=milestone.id, status="up_next"
        ),
    )
    store.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            expected_revision=task.revision,
            completion="code",
            stages=fixture_stage_change(store, "harbor", task.id),
        ),
    )
    run = execution.claim("harbor", base, {base: set()}, task_id=task.id)
    execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(store.directory, repo, run.id, base)
    (env.checkout / "feature.txt").write_text("Combined feature\n")
    candidate, _ = env.snapshot(base)
    execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(summary="Feature", checks="true"),
        commit=candidate,
    )
    results = Results(store)
    results.process("harbor")
    result = results.page("harbor", task.id).items[0]
    Reviewers(store).configure(
        "harbor",
        "task",
        ReviewerEdit(
            expected_revision=1, enabled=True, selection=AgentChoice(model="review-fixture")
        ),
    )
    return store, repo, base, milestone, delivery, results, result


def policy(store, milestone, delivery, enabled):
    return MilestoneDeliveries(store).integration_policy(
        "harbor",
        milestone.id,
        TaskIntegrationPolicyEdit(
            expected_revision=delivery.integration_policy.revision,
            delivery_id=delivery.id,
            branch=delivery.branch,
            enabled=enabled,
        ),
    )


def assess(store, result):
    reviews = TaskReviews(store)
    review = reviews.prepare("harbor", result.id)
    review = reviews.claim("harbor", review.id, review.revision)
    review = reviews.started("harbor", review.id, review.revision)
    return reviews.conclude("harbor", review.id, review.revision, verdict(), cleanup_confirmed=True)


def test_explicit_grant_delivers_once_only_to_owned_branch(tmp_path):
    store, repo, base, milestone, delivery, results, result = prepared(tmp_path)
    assert not delivery.integration_policy.enabled
    delivery = policy(store, milestone, delivery, True)
    review = assess(store, result)
    reviews = TaskReviews(store)
    applied = reviews.delegate("harbor", review.id)
    assert applied.applied_at and results.get("harbor", result.id).delegated_review_id == review.id
    assert reviews.delegate("harbor", review.id) == applied
    results.process("harbor")
    assert results.get("harbor", result.id).status == "delivered"
    assert git(repo, "rev-parse", "main").decode().strip() == base
    assert git(repo, "rev-parse", delivery.branch).decode().strip() == result.candidate_commit
    with store.connection() as db:
        assert (
            db.execute("SELECT count(*) FROM milestone_integration_policy_history").fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("when", ["before_review", "after_review", "after_decision"])
def test_revocation_cannot_transfer_old_authority(tmp_path, when):
    store, repo, base, milestone, delivery, results, result = prepared(tmp_path)
    delivery = policy(store, milestone, delivery, True)
    if when == "before_review":
        delivery = policy(store, milestone, delivery, False)
    review = assess(store, result)
    if when == "after_review":
        delivery = policy(store, milestone, delivery, False)
    TaskReviews(store).delegate("harbor", review.id)
    if when == "after_decision":
        policy(store, milestone, delivery, False)
    results.process("harbor")
    assert results.get("harbor", result.id).status != "delivered"
    assert git(repo, "rev-parse", delivery.branch).decode().strip() == base


def test_checkout_retry_retains_exact_delegated_approval(tmp_path):
    store, _, _, milestone, delivery, results, result = prepared(tmp_path)
    policy(store, milestone, delivery, True)
    review = assess(store, result)
    TaskReviews(store).delegate("harbor", review.id)
    original = results.get("harbor", result.id)
    dirty = Path(delivery.path) / "README.md"
    dirty.write_text("human edits\n")
    results.process("harbor")
    blocked = results.get("harbor", result.id)
    assert blocked.status == "stale"
    dirty.write_text("Existing project\n")
    results.retry_delivery("harbor", result.id, RunAction(expected_revision=blocked.revision))
    results.process("harbor")
    saved = results.get("harbor", result.id)
    assert saved.status == "delivered", saved.problem
    assert saved.approved_at == original.approved_at and saved.delegated_review_id == review.id


def test_crash_after_git_recovers_without_reapplying_revoked_policy(tmp_path, monkeypatch):
    from flowfield.adapters import git_integration

    store, repo, _, milestone, delivery, results, result = prepared(tmp_path)
    delivery = policy(store, milestone, delivery, True)
    review = assess(store, result)
    TaskReviews(store).delegate("harbor", review.id)
    original = git_integration.apply

    def crash(*args):
        original(*args)
        raise RuntimeError("simulated service exit after Git")

    monkeypatch.setattr(git_integration, "apply", crash)
    with pytest.raises(RuntimeError):
        results.process("harbor")
    policy(store, milestone, delivery, False)
    results.process("harbor")
    assert results.get("harbor", result.id).status == "delivered"
    assert git(repo, "rev-parse", delivery.branch).decode().strip() == result.candidate_commit


def test_disabled_review_does_not_authorize_unreviewed_delivery(tmp_path):
    store, repo, base, milestone, delivery, results, result = prepared(tmp_path)
    policy(store, milestone, delivery, True)
    Reviewers(store).configure("harbor", "task", ReviewerEdit(expected_revision=2, enabled=False))
    with pytest.raises(ApplicationError, match="disabled"):
        TaskReviews(store).prepare("harbor", result.id)
    results.process("harbor")
    assert not results.get("harbor", result.id).approved_at
    assert git(repo, "rev-parse", delivery.branch).decode().strip() == base


def test_policy_migration_defaults_off_and_rolls_back(tmp_path, monkeypatch):
    from flowfield import migrations, storage
    from flowfield.application import Workspace
    from flowfield.migrations import Migration
    from flowfield.migrations.v076_delegated_integration import apply

    old_chain = tuple(m for m in migrations.MIGRATIONS if m.version <= 75)
    with monkeypatch.context() as old:
        old.setattr(migrations, "MIGRATIONS", old_chain)
        store, _, _, milestone, _, _, _ = prepared(tmp_path)

    def interrupted(db):
        apply(db)
        raise RuntimeError("Interrupted policy migration")

    with monkeypatch.context() as broken:
        broken.setattr(migrations, "MIGRATIONS", (*old_chain, Migration(76, interrupted)))
        with pytest.raises(Exception, match="Interrupted policy migration"):
            Workspace(store.directory)
    with storage.connect(store.directory / storage.DATABASE) as db:
        assert storage.version(db) == 75
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='milestone_integration_policy_history'"
        ).fetchone()
    upgraded = Workspace(store.directory)
    delivery = MilestoneDeliveries(upgraded).get("harbor", milestone.id)
    assert not delivery.integration_policy.enabled
    with upgraded.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_sibling_delivery_reassembles_and_requires_fresh_review(tmp_path):
    from test_milestone_delivery_git import commit

    store, repo, base, milestone, delivery, results, result = prepared(tmp_path)
    policy(store, milestone, delivery, True)
    old_review = assess(store, result)
    reviews = TaskReviews(store)
    commit(Path(delivery.path), "sibling.txt", "A sibling contribution\n")
    assert reviews.check("harbor", old_review.id).status == "stale"
    assert reviews.refresh_destination("harbor", result.id)
    results.process("harbor")
    fresh = results.page("harbor", result.task_id).items[0]
    assert fresh.id != result.id and fresh.candidate_commit != result.candidate_commit
    assert not fresh.approved_at
    current_review = assess(store, fresh)
    reviews.delegate("harbor", current_review.id)
    results.process("harbor")
    assert results.get("harbor", fresh.id).status == "delivered"
    assert git(repo, "rev-parse", "main").decode().strip() == base
    assert (Path(delivery.path) / "sibling.txt").exists()
    assert (Path(delivery.path) / "feature.txt").exists()
