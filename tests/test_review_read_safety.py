"""GET projections must not invalidate reviews while another transaction owns writes."""

import sqlite3

import pytest
from result_fixtures import current
from result_recovery_fixtures import target_change
from task_reviews_fixtures import configured, verdict

from flowfield.attention import attention_page
from flowfield.browser import BrowserReads
from flowfield.execution_models import RunAction


def test_stale_review_reads_are_pure_with_a_concurrent_writer(tmp_path):
    service, repo, run, _, reviews = configured(tmp_path)
    result = current(service)
    review = reviews.prepare("harbor", result.id)
    claimed = reviews.claim("harbor", review.id, review.revision)
    started = reviews.started("harbor", review.id, claimed.revision)
    decided = reviews.conclude(
        "harbor", review.id, started.revision, verdict(), cleanup_confirmed=True
    )
    target_change(service, repo, run.base_commit, "other.txt", "advanced destination\n")
    before = reviews.get("harbor", review.id)
    assert before.status == "decided"
    # A RESERVED writer lock allows reads but makes upgrading a reader to a writer
    # fail immediately. This reproduces the actual approval/refresh race.
    with service.workspace.connection(write=True) as writer:
        revision = writer.execute("SELECT revision FROM storage_metadata").fetchone()[0]
        for _ in range(2):
            board = BrowserReads(service.workspace).workspace_board("harbor")
            assert board.tasks
            attention_page(service.workspace, "harbor", "action")
            assert current(service).next_action.label == "Awaiting review"
        assert writer.execute("SELECT revision FROM storage_metadata").fetchone()[0] == revision
    assert reviews.get("harbor", review.id) == before == decided
    # The service still persists invalidation through its explicit write owner.
    assert reviews.check("harbor", review.id).status == "stale"


def test_stale_result_with_active_reviewer_is_not_a_human_recovery(tmp_path):
    service, repo, run, _, reviews = configured(tmp_path)
    result = current(service)
    review = reviews.prepare("harbor", result.id)
    claimed = reviews.claim("harbor", review.id, review.revision)
    running = reviews.started("harbor", review.id, claimed.revision)
    target_change(service, repo, run.base_commit, "other.txt", "advanced destination\n")
    service.integrations.refresh_availability("harbor")
    stale = current(service)
    assert stale.status == "stale"
    assert reviews.latest("harbor", run.task_id, current_only=True).id == running.id
    assert stale.next_action.owner == "reviewer"
    assert stale.next_action.action == "wait"
    assert attention_page(service.workspace, "harbor", "action").total == 0
    assert BrowserReads(service.workspace).workspace_board("harbor").needs_you_count == 0
    reviews.conclude("harbor", review.id, running.revision, verdict(), cleanup_confirmed=True)
    assert current(service).next_action.action == "prepare"
    refreshed = service.results.reprepare(
        "harbor", result.id, RunAction(expected_revision=current(service).revision)
    )
    assert refreshed.status == "preparing"
    assert service.workspace.task("harbor", run.task_id).status == "in_progress"


def test_read_connections_reject_accidental_mutation(tmp_path):
    service, *_ = configured(tmp_path)
    with service.workspace.connection() as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE storage_metadata SET revision=revision+1")


def test_superseded_stopped_review_is_history_not_current_recovery(tmp_path):
    from flowfield.result_models import ResultReview

    service, repo, run, _, reviews = configured(tmp_path)
    result = current(service)
    review = reviews.prepare("harbor", result.id)
    stopped = reviews.stop("harbor", review.id, review.revision, cleanup_confirmed=True)
    assert reviews.latest("harbor", run.task_id, current_only=True) == stopped
    service.results.review(
        "harbor",
        result.id,
        ResultReview(
            expected_revision=result.revision,
            candidate_commit=result.candidate_commit,
            action="request_changes",
            note="Finish the remaining behavior",
        ),
    )
    with service.workspace.connection(write=True) as writer:
        revision = writer.execute("SELECT revision FROM storage_metadata").fetchone()[0]
        assert reviews.latest("harbor", run.task_id, current_only=True) is None
        assert writer.execute("SELECT revision FROM storage_metadata").fetchone()[0] == revision
    assert reviews.get("harbor", review.id) == stopped
    assert reviews.latest("harbor", run.task_id) == stopped
    fresh = service.execution.claim("harbor", run.base_commit, {run.base_commit: set()})
    assert fresh
    assert reviews.latest("harbor", run.task_id, current_only=True) is None
    assert reviews.get("harbor", review.id) == stopped


def test_review_policy_changes_and_review_completion_persist_task_phases(tmp_path):
    from flowfield.reviewer_settings import ReviewerEdit

    service, _, _, reviewers, reviews = configured(tmp_path)
    # The fixture enabled review after an unreviewed candidate was already in review.
    assert service.workspace.task("harbor", "work").status == "in_review"
    result = current(service)
    review = reviews.prepare("harbor", result.id)
    assert service.workspace.task("harbor", "work").status == "in_progress"
    claimed = reviews.claim("harbor", review.id, review.revision)
    running = reviews.started("harbor", review.id, claimed.revision)
    reviews.conclude("harbor", review.id, running.revision, verdict(), cleanup_confirmed=True)
    assert service.workspace.task("harbor", "work").status == "in_review"
    assert current(service).next_action.action == "review_result"
    # Policy changes have the same phase owner as initial preparation and rechecking.
    settings = reviewers.get("harbor")
    reviewers.configure("harbor", ReviewerEdit(expected_revision=settings.revision, enabled=False))
    service.results.synchronize_readiness("harbor", result.id)
    assert service.workspace.task("harbor", "work").status == "in_review"


def test_partial_result_without_reviewer_waits_for_human_in_review(tmp_path):
    from result_fixtures import fixture

    service, _, _ = fixture(tmp_path, outcome="partial", remaining_work="Finish error handling.")
    service.results.process("harbor")
    assert current(service).next_action.action == "request_changes"
    assert service.workspace.task("harbor", "work").status == "in_review"


@pytest.mark.parametrize("phase", ["backlog", "up_next", "done"])
def test_readiness_does_not_reopen_or_start_a_task_from_retained_evidence(tmp_path, phase):
    service, *_ = configured(tmp_path)
    result = current(service)
    with service.workspace.connection(write=True) as db:
        task = service.workspace._task(db, "harbor", "work")
        service.execution._task_state(db, task, phase, "Human changed the task lifecycle.")
    before = service.workspace.task("harbor", "work")
    service.results.synchronize_readiness("harbor", result.id)
    assert service.workspace.task("harbor", "work") == before
