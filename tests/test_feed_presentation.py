"""Feed attribution and live state must not rewrite or reanimate historical evidence."""

import pytest
from execution_fixtures import BASE, fixture

from flowfield.actors import actor
from flowfield.execution_models import RunAction
from flowfield.questions import QuestionCreate
from flowfield.thread_view import ThreadView
from flowfield.work_state import WorkState, event_state


@pytest.mark.parametrize(
    "identity,role,label",
    [
        ("human", "human", "You"),
        ("agent", "coordinator", "Coordinator"),
        ("coordinator", "coordinator", "Coordinator"),
        ("worker:abc", "worker", "Worker"),
        ("service", "flowfield", "Flowfield"),
        ("flowfield", "flowfield", "Flowfield"),
        ("Custom reviewer", "other", "Custom reviewer"),
    ],
)
def test_actor_preserves_identity(identity, role, label):
    value = actor(identity)
    assert (value.identity, value.role, value.label) == (identity, role, label)


def test_only_current_question_revision_needs_attention():
    current = WorkState(
        label="Needs your answer", tone="attention", href="/conversation/question:q:3"
    )
    assert event_state("question", "open", "question:q:3", current).tone == "attention"
    assert event_state("question", "open", "question:q:1", current).tone == "complete"
    assert event_state("answer", "answered", "answer:q:2", current).tone == "complete"
    attempt = event_state("attempt", "waiting_for_input", "attempt:a", current)
    assert attempt.label == "Asked a question" and attempt.tone == "idle"


def test_question_prompt_is_body_content_not_event_title(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    question = execution.ask_question(
        "harbor",
        run.id,
        QuestionCreate(
            task_id="task-0",
            question="Which output format should we use?",
            context="Two formats are possible.",
            recommendation="Plain text",
        ),
    )
    message = ThreadView(execution.workspace).item_view(
        "harbor", "task-0", f"question:{question.id}:{question.revision}"
    )
    assert message.title == "Question"
    assert message.question == question.question
    assert message.body == "Two formats are possible.\n\nRecommendation: Plain text"


def test_feed_tracks_owner_but_keeps_old_attempt_and_definition_static(tmp_path):
    execution = fixture(tmp_path)
    thread = ThreadView(execution.workspace)
    first = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", first.id)
    live = thread.item_view("harbor", "task-0", "attempt:" + first.id)
    assert live.state.tone == "active" and live.actor.role == "worker"
    execution.finish("harbor", first.id, "stopped")
    stopped = execution.get("harbor", first.id)
    execution.retry("harbor", first.id, RunAction(expected_revision=stopped.revision))
    second = execution.claim("harbor", BASE, {BASE: set()})
    assert second and second.id != first.id
    page = thread.page_view("harbor", "task-0")
    by_id = {item.id: item for item in page.items}
    assert by_id["attempt:" + first.id].state.tone == "idle"
    assert by_id["attempt:" + first.id].state.label == "Stopped"
    assert by_id["attempt:" + second.id].state.tone == "active"
    assert all(item.state.tone == "complete" for item in page.items if item.kind == "definition")


def test_historical_result_cannot_inherit_new_check_cycle():
    current = WorkState(label="Checking changes", tone="active", href="/conversation/result:new")
    assert event_state("result", "preparing", "result:old", current).tone == "idle"
    assert event_state("result", "preparing", "result:new", current).tone == "active"
    assert event_state("approval", "delivering", "approval:new", current).tone == "complete"


def test_uncertain_worker_needs_attention_without_animation():
    current = WorkState(
        label="Check worker state", tone="attention", href="/conversation/attempt:a"
    )
    assert event_state("attempt", "uncertain", "attempt:a", current).tone == "attention"
    assert event_state("state", "in_progress", "definition:2", current).tone == "complete"


def test_recorded_withdrawals_and_handoff_warnings_remain_visible():
    current = WorkState(label="Done", tone="complete")
    assert event_state("question", "withdrawn", "question:q:2", current).label == "Withdrawn"
    assert event_state("activity", "superseded", "activity:a", current).label == "Superseded"
    assert (
        event_state(
            "activity", "Task changed · Recheck before continuing", "activity:b", current
        ).label
        == "Task changed · Recheck before continuing"
    )
