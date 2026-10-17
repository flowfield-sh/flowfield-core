"""Human policy, exact review and Git recovery use the existing delivery owner."""

from pathlib import Path

import pytest
from delegated_integration_fixtures import assess, policy, prepared, workspace_policy

from flowfield.adapters.git_workspace import git
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.milestone_delivery import MilestoneDeliveries
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.task_reviews import TaskReviews


def test_refresh_does_not_invalidate_a_milestone_candidate_prepared_during_observation(
    tmp_path, monkeypatch
):
    from flowfield.integration import Integrations

    store, _, _, _, delivery, results, result = prepared(
        tmp_path, validate=False, prior_report=True
    )
    integrations = Integrations(store)
    original = integrations.available

    def finish_preparation(project_id, head, branch=None):
        results.process(project_id)
        return original(project_id, head, branch)

    # Accepted history makes this an active refresh. The first ready code candidate
    # introduces a branch after refresh has read its branch list.
    monkeypatch.setattr(integrations, "available", finish_preparation)
    integrations.refresh_availability("harbor")
    current = results.get("harbor", result.id)
    assert current.status == "ready", current.problem
    assert integrations.get("harbor", current.integration_id).target_branch == delivery.branch
    integrations.refresh_availability("harbor")
    assert results.get("harbor", result.id).status == "ready"


def test_explicit_grant_delivers_once_only_to_owned_branch(tmp_path):
    store, repo, base, milestone, delivery, results, result = prepared(tmp_path)
    assert not delivery.integration_policy.enabled
    delivery = policy(store, milestone, delivery, True)
    review = assess(store, result)
    reviews = TaskReviews(store)
    applied = reviews.delegate("harbor", review.id)
    assert (
        applied.integration_applied_at
        and results.get("harbor", result.id).delegated_review_id == review.id
    )
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


def test_sibling_delivery_reassembles_and_requires_fresh_review(tmp_path):
    from milestone_delivery_git_fixtures import commit

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


def test_native_conclusion_is_followed_by_one_delegated_decision(tmp_path, monkeypatch):
    import asyncio

    from review_execution_fixtures import install, round_trip

    from flowfield.supervisor import Supervisor

    store, repo, base, milestone, delivery, results, result = prepared(tmp_path)
    policy(store, milestone, delivery, True)
    install(monkeypatch)
    service = Supervisor(store)
    asyncio.run(round_trip(service))
    review = TaskReviews(store).latest("harbor", result.task_id)
    assert review.status == "decided" and review.applied_at
    assert not review.integration_applied_at and not results.get("harbor", result.id).approved_at
    asyncio.run(round_trip(service))
    results.process("harbor")
    assert results.get("harbor", result.id).status == "delivered"
    assert TaskReviews(store).get("harbor", review.id).integration_applied_at
    assert git(repo, "rev-parse", "main").decode().strip() == base


def test_workspace_default_delegates_only_owned_branch(tmp_path):
    store, repo, base, milestone, delivery, results, result = prepared(tmp_path)
    assert delivery.integration_policy.enabled is None
    workspace_policy(store, True)
    view = MilestoneDeliveries(store).policy_view("harbor", milestone.id)
    assert view.enabled is None and view.effective_enabled and view.workspace_revision == 2
    review = assess(store, result)
    assert review.binding.workspace_integration_policy_revision == 2
    TaskReviews(store).delegate("harbor", review.id)
    results.process("harbor")
    assert results.get("harbor", result.id).status == "delivered"
    assert git(repo, "rev-parse", "main").decode().strip() == base


def test_workspace_revocation_invalidates_pending_delivery(tmp_path):
    store, repo, base, _, delivery, results, result = prepared(tmp_path)
    workspace_policy(store, True)
    review = assess(store, result)
    TaskReviews(store).delegate("harbor", review.id)
    workspace_policy(store, False, 2)
    workspace_policy(store, True, 3)
    results.process("harbor")
    assert results.get("harbor", result.id).status == "stale"
    assert git(repo, "rev-parse", delivery.branch).decode().strip() == base


def test_override_survives_defaults_and_reset_inherits_current_policy(tmp_path):
    store, _, _, milestone, delivery, _, result = prepared(tmp_path)
    delivery = policy(store, milestone, delivery, True)
    review = assess(store, result)
    workspace_policy(store, False)
    assert TaskReviews(store).check("harbor", review.id).status == "decided"
    delivery = policy(store, milestone, delivery, None)
    view = MilestoneDeliveries(store).policy_view("harbor", milestone.id)
    assert view.enabled is None and not view.effective_enabled
    assert TaskReviews(store).check("harbor", review.id).status == "stale"
    workspace_policy(store, True, 2)
    assert MilestoneDeliveries(store).policy_view("harbor", milestone.id).effective_enabled


def test_workspace_defaults_are_isolated_and_revision_guarded(tmp_path):
    from flowfield.delivery_models import WorkspaceIntegrationPolicyEdit
    from flowfield.workspaces import WorkspaceCreate, Workspaces

    store, _, _, milestone, _, _, _ = prepared(tmp_path)
    other = Workspaces(store).create(WorkspaceCreate(name="Other workspace"))
    owner = MilestoneDeliveries(store)
    owner.configure_workspace_policy(
        other.id, WorkspaceIntegrationPolicyEdit(expected_revision=1, enabled=True)
    )
    assert not owner.policy_view("harbor", milestone.id).effective_enabled
    with pytest.raises(ApplicationError, match="stale"):
        owner.configure_workspace_policy(
            other.id, WorkspaceIntegrationPolicyEdit(expected_revision=1, enabled=False)
        )
    assert owner.workspace_policy(other.id).enabled
    with pytest.raises(ApplicationError, match="not found"):
        owner.policy_view(other.id, milestone.id)
