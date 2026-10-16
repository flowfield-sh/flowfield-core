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


def prepared(tmp_path, *, validate=True, prior_report=False):
    execution, integrations, repo, base, milestone, delivery = setup(tmp_path)
    store = execution.workspace
    if prior_report:
        report = store.create_task(
            "harbor",
            task_request(
                title="Earlier report", body="Report repository findings", status="up_next"
            ),
        )
        store.publish_task(
            "harbor",
            report.id,
            TaskPublish(expected_revision=report.revision, completion="report"),
        )
        attempt = execution.claim("harbor", base, {base: set()}, task_id=report.id)
        assert attempt
        execution.started("harbor", attempt.id)
        execution.finish(
            "harbor",
            attempt.id,
            "in_review",
            commit=base,
            result=WorkerResult(summary="Earlier findings", checks="Read the repository"),
        )
        Results(store).process("harbor")
        assert execution.get("harbor", attempt.id).status == "accepted"
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
    if validate:
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


def test_native_conclusion_is_followed_by_one_delegated_decision(tmp_path, monkeypatch):
    import asyncio

    from test_review_execution import install, round_trip

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


def workspace_policy(store, enabled, revision=1):
    from flowfield.delivery_models import WorkspaceIntegrationPolicyEdit

    return MilestoneDeliveries(store).configure_workspace_policy(
        "harbor", WorkspaceIntegrationPolicyEdit(expected_revision=revision, enabled=enabled)
    )


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


def test_workspace_policy_migration_preserves_overrides_and_recovers(tmp_path, monkeypatch):
    from flowfield import migrations, storage
    from flowfield.application import Workspace
    from flowfield.migrations import Migration
    from flowfield.migrations.v078_workspace_integration import apply

    old_chain = tuple(m for m in migrations.MIGRATIONS if m.version <= 77)
    with monkeypatch.context() as old:
        old.setattr(migrations, "MIGRATIONS", old_chain)
        store, _, _, milestone, delivery, _, _ = prepared(tmp_path)
        delivery = policy(store, milestone, delivery, True)

    def interrupted(db):
        apply(db)
        raise RuntimeError("Interrupted default migration")

    with monkeypatch.context() as broken:
        broken.setattr(migrations, "MIGRATIONS", (*old_chain, Migration(78, interrupted)))
        with pytest.raises(Exception, match="Interrupted default migration"):
            Workspace(store.directory)
    with storage.connect(store.directory / storage.DATABASE) as db:
        assert storage.version(db) == 77
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='workspace_integration_policies'"
        ).fetchone()
    upgraded = Workspace(store.directory)
    view = MilestoneDeliveries(upgraded).policy_view("harbor", milestone.id)
    assert view.enabled is True and view.effective_enabled
    assert view.revision == delivery.integration_policy.revision
    workspace_policy(upgraded, False)
    assert MilestoneDeliveries(upgraded).policy_view("harbor", milestone.id).effective_enabled


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


def test_migration_distinguishes_untouched_policy_from_explicit_off(tmp_path, monkeypatch):
    import json

    from flowfield import migrations
    from flowfield.application import MilestoneCreate, Workspace
    from flowfield.delivery_models import DeliveryConfigure
    from flowfield.milestones import Milestones

    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 77)
        )
        store, _, _, milestone, delivery, _, _ = prepared(tmp_path)
        other = Milestones(store).create("harbor", MilestoneCreate(title="Explicit off"))
        second = MilestoneDeliveries(store).configure(
            "harbor", other.id, DeliveryConfigure(expected_revision=1, project_id="harbor")
        )
        policy(store, other, second, False)
        # The previous model stored False for an untouched revision-one policy.
        with store.connection(write=True) as db:
            data = json.loads(
                db.execute(
                    "SELECT data FROM milestone_deliveries WHERE id=?", (delivery.id,)
                ).fetchone()[0]
            )
            data["integration_policy"]["enabled"] = False
            db.execute(
                "UPDATE milestone_deliveries SET data=? WHERE id=?", (json.dumps(data), delivery.id)
            )
    upgraded = Workspace(store.directory)
    workspace_policy(upgraded, True)
    owner = MilestoneDeliveries(upgraded)
    assert owner.policy_view("harbor", milestone.id).enabled is None
    assert owner.policy_view("harbor", milestone.id).effective_enabled
    assert owner.policy_view("harbor", other.id).enabled is False
    assert not owner.policy_view("harbor", other.id).effective_enabled
