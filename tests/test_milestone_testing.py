"""Human testing reuses exact observations; it never changes verdicts or approval."""

import asyncio

import pytest
from milestone_reviews_fixtures import fixture, install, latest, step
from replies_fixtures import message

from flowfield.errors import ApplicationError
from flowfield.milestone_verification import MilestoneVerifications
from flowfield.replies import Replies
from flowfield.reply_models import MilestoneObservationBinding


def ready(tmp_path, monkeypatch):
    service, _, _, milestone, _, _ = fixture(tmp_path)
    install(monkeypatch)
    asyncio.run(step(service))
    value = latest(service, milestone)
    return service.workspace, value


def test_exact_human_testing_retains_attribution_without_acceptance(tmp_path, monkeypatch):
    store, value = ready(tmp_path, monkeypatch)
    task = value.tasks[0]
    request = message(
        store, task.task_id, body="The human tried the complete pipeline: 19 cases passed."
    )
    request.author = "coordinator:reported-test"
    request.milestone = MilestoneObservationBinding(
        verification_id=value.id,
        verification_revision=value.revision,
        candidate_commit=value.promotion.candidate_commit,
    )
    reply = Replies(store).submit("harbor", task.task_id, request)
    assert Replies(store).submit("harbor", task.task_id, request) == reply
    owner = MilestoneVerifications(store)
    notes = owner.testing("harbor", value.id)
    assert len(notes.items) == 1 and notes.items[0].body == request.body
    assert notes.items[0].actor.label == "Coordinator"
    assert notes.items[0].result_id == task.result_id
    assert owner.get("harbor", value.id) == value
    assert value.status == "ready" and not value.reviewed_at
    assert value.task_titles[task.result_id] == "Contribution"
    with pytest.raises(ApplicationError, match="not found"):
        owner.testing("another-workspace", value.id)


def test_testing_rejects_wrong_code_and_revision(tmp_path, monkeypatch):
    store, value = ready(tmp_path, monkeypatch)
    task = value.tasks[0]
    request = message(store, task.task_id)
    request.milestone = MilestoneObservationBinding(
        verification_id=value.id,
        verification_revision=value.revision,
        candidate_commit="f" * 40,
    )
    with pytest.raises(ApplicationError, match="current milestone candidate"):
        Replies(store).submit("harbor", task.task_id, request)
    request.milestone.candidate_commit = value.promotion.candidate_commit
    request.milestone.verification_revision -= 1
    with pytest.raises(ApplicationError, match="stale"):
        Replies(store).submit("harbor", task.task_id, request)
    assert not MilestoneVerifications(store).testing("harbor", value.id).items


def test_legacy_observations_require_identical_candidate_and_page_without_loss(
    tmp_path, monkeypatch
):
    store, value = ready(tmp_path, monkeypatch)
    task = value.tasks[0]
    assert task.commit == value.promotion.candidate_commit
    for index in range(22):
        Replies(store).submit(
            "harbor", task.task_id, message(store, task.task_id, body=f"Observed case {index}")
        )
    owner = MilestoneVerifications(store)
    first = owner.testing("harbor", value.id)
    second = owner.testing("harbor", value.id, first.next_offset)
    assert len(first.items) == 20 and len(second.items) == 2 and second.next_offset is None
    assert len({note.id for note in first.items + second.items}) == 22
    # A different combined candidate must not inherit result-only observations,
    # even if every contributing result remains unchanged.
    with store.connection(write=True) as db:
        db.execute(
            "UPDATE milestone_promotions SET data=json_set(data,'$.candidate_commit',?) WHERE id=?",
            ("e" * 40, value.promotion_id),
        )
    assert not owner.testing("harbor", value.id).items
