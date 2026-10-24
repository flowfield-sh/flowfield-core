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
