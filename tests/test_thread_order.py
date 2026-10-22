"""Causal presentation and pagination when the clock gives equal timestamps."""

import json

from execution_fixtures import BASE, fixture

from flowfield.application import TaskEdit
from flowfield.conversation import Conversation
from flowfield.thread_view import ThreadView


def test_reservation_then_actual_start_and_tied_pagination(tmp_path, monkeypatch):
    execution = fixture(tmp_path)
    stamp = "2099-01-01T00:00:00.123456+00:00"
    monkeypatch.setattr("flowfield.execution.now", lambda: stamp)
    monkeypatch.setattr("flowfield.application.now", lambda: stamp)
    run = execution.claim("harbor", BASE, {BASE: set()})
    thread = ThreadView(execution.workspace)
    messages = thread.page_view("harbor", run.task_id).items
    attempt = next(m for m in messages if m.kind == "attempt")
    assert attempt.title == "Worker reserved"
    execution.started("harbor", run.id)
    conversation = Conversation(execution.workspace)
    page = conversation.page("harbor", run.task_id)
    ordered = sorted(page.items, key=lambda v: (v.created_at, v.order_key, v.id))
    movement = next(
        i for i, v in enumerate(ordered) if v.kind == "definition" and v.created_at == stamp
    )
    started = next(i for i, v in enumerate(ordered) if v.kind == "attempt")
    assert movement < started
    all_ids = [v.id for v in page.items]
    ids, cursor = [], None
    while True:
        page = conversation.page("harbor", run.task_id, cursor, limit=1)
        ids.extend(v.id for v in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert ids == all_ids
    # Existing bookmarks with the older two-field cursor remain valid.
    boundary = ordered[-1]
    legacy = conversation.page(
        "harbor", run.task_id, json.dumps([boundary.created_at, boundary.id])
    )
    assert [v.id for v in legacy.items] == all_ids[1:]


def test_equal_time_definition_revisions_use_numeric_order(tmp_path, monkeypatch):
    execution = fixture(tmp_path)
    monkeypatch.setattr("flowfield.application.now", lambda: "2099-01-01T00:00:00+00:00")
    for index in range(12):
        task = execution.workspace.task("harbor", "task-0")
        execution.workspace.edit_task(
            "harbor", task.id, TaskEdit(expected_revision=task.revision, title=f"Title {index}")
        )
    items = Conversation(execution.workspace).page("harbor", "task-0", limit=30).items
    revisions = [i.revision for i in items if i.kind == "definition"]
    assert revisions == sorted(revisions, reverse=True)
