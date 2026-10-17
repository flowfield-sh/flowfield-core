"""Real Git combination, exact verifier baseline and independent promotion approval."""

import pytest
from milestone_delivery_fixtures import contribute, setup
from milestone_delivery_git_fixtures import commit
from project_fixtures import task_request

from flowfield.adapters.git_workspace import baseline
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerResult
from flowfield.milestone_promotion import MilestonePromotions
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.results import Results
from flowfield.verification_models import (
    VerificationPrepare,
    VerificationReport,
    VerificationReview,
)


def prepared(tmp_path):
    execution, integrations, repo, base, milestone, delivery = setup(tmp_path)
    first, _, result = contribute(execution, integrations, repo, milestone, "feature.txt")
    # Independent destination work makes the final candidate a real combination.
    destination = commit(repo, "parallel.txt", "Other work\n")
    store = execution.workspace
    task = store.create_task(
        "harbor",
        task_request(
            title="Verify whole outcome",
            body="Check the feature with current main",
            milestone_id=milestone.id,
            dependencies=[first.id],
            status="up_next",
        ),
    )
    records = MilestoneVerifications(store)
    verification = records.prepare(
        "harbor",
        milestone.id,
        VerificationPrepare(
            expected_revision=1,
            project_id="harbor",
            task_id=task.id,
            expected_task_revision=task.revision,
            checks=["Feature works with main"],
        ),
    )
    assert verification.status == "preparing"
    assert (
        execution.claim(
            "harbor",
            result.candidate_commit,
            {result.candidate_commit: {result.source_commit}},
            task_id=task.id,
        )
        is None
    )
    promotions = MilestonePromotions(store)
    promotions.process("harbor")
    verification = records.get("harbor", verification.id)
    assert verification.status == "verifying", verification.problem
    candidate = verification.promotion.candidate_commit
    assert candidate not in (destination, result.candidate_commit)
    assert baseline(repo) == destination
    assert integrations.head("harbor", task.id) == candidate
    available = integrations.available("harbor", candidate, delivery.branch)
    run = execution.claim("harbor", candidate, {candidate: available}, task_id=task.id)
    assert run and run.base_commit == candidate
    execution.started("harbor", run.id)
    execution.finish(
        "harbor",
        run.id,
        "in_review",
        commit=candidate,
        result=WorkerResult(
            summary="Combined feature verified",
            checks="true",
            verification=VerificationReport(
                verification_id=verification.id,
                findings=[{"check": 1, "verdict": "passed", "evidence": "Both files present"}],
                coverage="complete",
                coverage_notes="Full feature and existing work checked",
            ),
        ),
    )
    Results(store).process("harbor")
    verification = records.get("harbor", verification.id)
    assert verification.status == "ready", verification.problem
    return records, promotions, repo, destination, verification


def accept(records, value):
    return records.review(
        "harbor", value.id, VerificationReview(expected_revision=value.revision, action="accept")
    )


def test_exact_combination_verified_then_promoted(tmp_path):
    records, promotions, repo, destination, verification = prepared(tmp_path)
    approved = accept(records, verification)
    assert approved.status == "promoting" and baseline(repo) == destination
    promotions.process("harbor")
    delivered = records.get("harbor", verification.id)
    assert delivered.status == "accepted", delivered.problem
    assert baseline(repo) == verification.promotion.candidate_commit
    assert (repo / "feature.txt").exists() and (repo / "parallel.txt").exists()
    assert delivered.promotion.approved_at and delivered.promotion.delivered_at


def test_dirty_destination_preserves_approval_for_retry(tmp_path):
    from flowfield.attention import attention_page

    records, promotions, repo, destination, verification = prepared(tmp_path)
    (repo / "README.md").write_text("Unfinished human work\n")
    accept(records, verification)
    assert not attention_page(records.store, None, workspace_id="harbor", column="action").items
    waiting = attention_page(records.store, None, workspace_id="harbor", column="waiting").items
    assert any(item.next_action == "Promoting verified milestone" for item in waiting)
    promotions.process("harbor")
    blocked = records.get("harbor", verification.id)
    assert blocked.status == "delivery_blocked", blocked.problem
    assert baseline(repo) == destination
    inbox = attention_page(records.store, None, workspace_id="harbor", column="action").items
    assert len(inbox) == 1 and inbox[0].next_action == "Retry milestone promotion"
    approval = blocked.promotion.approved_at
    (repo / "README.md").write_text("Existing project\n")
    records.review(
        "harbor",
        blocked.id,
        VerificationReview(expected_revision=blocked.revision, action="retry_promotion"),
    )
    promotions.process("harbor")
    delivered = records.get("harbor", verification.id)
    assert delivered.status == "accepted", delivered.problem
    assert delivered.promotion.approved_at == approval
    assert not attention_page(records.store, None, workspace_id="harbor", column="action").items


def test_destination_movement_requires_new_verification(tmp_path):
    records, promotions, repo, _, verification = prepared(tmp_path)
    moved = commit(repo, "late.txt", "Later work\n")
    with pytest.raises(ApplicationError, match="destination moved"):
        accept(records, verification)
    promotions.process("harbor")
    assert records.get("harbor", verification.id).status == "stale"
    assert baseline(repo) == moved


@pytest.mark.parametrize("changed", ["source", "settings"])
def test_changed_source_or_checks_cannot_reuse_whole_outcome_approval(tmp_path, changed):
    from pathlib import Path

    from flowfield.integration_models import IntegrationConfig
    from flowfield.milestone_delivery import MilestoneDeliveries

    records, promotions, repo, destination, verification = prepared(tmp_path)
    accept(records, verification)
    if changed == "source":
        delivery = MilestoneDeliveries(records.store).get("harbor", verification.milestone_id)
        commit(Path(delivery.path), "unverified.txt", "Later milestone work\n")
    else:
        settings = promotions.integrations.settings("harbor")
        promotions.integrations.configure(
            "harbor",
            IntegrationConfig(
                expected_revision=settings.revision,
                target_branch=settings.target_branch,
                checks=["false"],
            ),
        )
    promotions.process("harbor")
    stale = records.get("harbor", verification.id)
    assert stale.status == "stale" and not stale.current
    assert stale.promotion.approved_at and not stale.promotion.delivered_at
    assert baseline(repo) == destination
    assert not (repo / "feature.txt").exists()


def test_restart_after_git_update_recovers_receipt_without_second_delivery(tmp_path, monkeypatch):
    records, promotions, repo, _, verification = prepared(tmp_path)
    accept(records, verification)
    original = promotions._save

    def crash_after_git(value):
        if value.status == "delivered":
            raise RuntimeError("exit after Git before receipt")
        original(value)

    monkeypatch.setattr(promotions, "_save", crash_after_git)
    with pytest.raises(RuntimeError, match="before receipt"):
        promotions.process("harbor")
    assert baseline(repo) == verification.promotion.candidate_commit
    assert records.get("harbor", verification.id).status == "promoting"
    MilestonePromotions(records.store).process("harbor")
    recovered = records.get("harbor", verification.id)
    assert recovered.status == "accepted"
    assert recovered.promotion.delivered_at


def test_movement_after_approval_never_promotes_unverified_combination(tmp_path):
    records, promotions, repo, _, verification = prepared(tmp_path)
    accept(records, verification)
    changed = commit(repo, "later.txt", "After approval\n")
    promotions.process("harbor")
    assert records.get("harbor", verification.id).status == "stale"
    assert baseline(repo) == changed
    assert not (repo / "feature.txt").exists()


def test_try_milestone_preserves_edited_copy_and_exact_candidate(tmp_path):
    from pathlib import Path

    from flowfield.inspection import Inspections

    records, _, repo, destination, verification = prepared(tmp_path)
    inspections = Inspections(records.store)
    first = inspections.prepare_milestone("harbor", verification.id, verification.revision)
    assert first.status == "ready" and first.promotion_id == verification.promotion_id
    assert baseline(Path(first.workspace)) == verification.promotion.candidate_commit
    (Path(first.workspace) / "feature.txt").write_text("human experiment\n")
    again = inspections.prepare_milestone("harbor", verification.id, verification.revision)
    assert again.id == first.id and again.locally_changed
    fresh = inspections.prepare_milestone("harbor", verification.id, verification.revision, True)
    assert fresh.id != first.id and not fresh.locally_changed
    assert (Path(first.workspace) / "feature.txt").read_text() == "human experiment\n"
    assert baseline(repo) == destination
    assert records.get("harbor", verification.id).status == "ready"


def test_blocked_promotion_expires_when_destination_changes(tmp_path):
    records, promotions, repo, _, verification = prepared(tmp_path)
    (repo / "README.md").write_text("Human edit\n")
    accept(records, verification)
    promotions.process("harbor")
    assert records.get("harbor", verification.id).status == "delivery_blocked"
    commit(repo, "README.md", "Human committed edit\n")
    promotions.process("harbor")
    stale = records.get("harbor", verification.id)
    assert stale.status == "stale" and not stale.current
    assert "destination moved" in stale.problem
