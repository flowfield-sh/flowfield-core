"""Public output, replay, bounded storage and shared state; no model calls."""

import asyncio
import sqlite3
import threading

import pytest
from test_execution import BASE, fixture
from test_input_continuation import question

from flowfield.application import Workspace
from flowfield.attention import attention_notices, attention_page
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit
from flowfield.questions import QuestionCreate, Questions
from flowfield.run_activity import MAX_TEXT, ActivityRecorder, ActivityUpdate, RunActivity


def test_bounded_snapshot_replays_after_restart_and_rejects_late_output(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {})
    store = RunActivity(execution.workspace)
    activity = []
    execution.workspace.on_activity = lambda project, attempt: activity.append((project, attempt))
    invalidations = []
    execution.workspace.on_change = invalidations.append
    store.write("harbor", run.id, [ActivityUpdate(key="agent", kind="agent", text="Hello")])
    store.write(
        "harbor", run.id, [ActivityUpdate(key="agent", kind="agent", text=" world", append=True)]
    )
    assert activity == [("harbor", run.id), ("harbor", run.id)]
    assert not invalidations
    read = store.read("harbor", run.id)
    assert read.items[0].text == "Hello world" and read.active and read.supported
    assert not store.read("harbor", run.id, read.revision).changed
    restored = RunActivity(Workspace(execution.workspace.directory)).read("harbor", run.id)
    assert restored == read
    store.write(
        "harbor",
        run.id,
        [ActivityUpdate(key=str(i), kind="output", text="x" * 9000) for i in range(120)],
    )
    read = store.read("harbor", run.id)
    assert read.omitted and all(e.omitted for e in read.items)
    assert len(read.items) <= 100 and sum(len(e.text) for e in read.items) <= 60000
    execution.finish("harbor", run.id, "uncertain")
    store.write("harbor", run.id, [ActivityUpdate(key="late", kind="agent", text="Ignore")])
    assert store.read("harbor", run.id).revision == read.revision
    assert not store.read("harbor", run.id).active
    with pytest.raises(ApplicationError):
        store.read("another-project", run.id)


def test_buffer_drains_and_marks_overflow_without_board_invalidations(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {})

    async def exercise():
        recorder = ActivityRecorder(execution.workspace, "harbor", run.id)
        for i in range(250):
            recorder.emit(ActivityUpdate(key=str(i), kind="output", text="\x1b[31moutput\x1b[0m"))
        assert len(recorder.pending) == 100
        await recorder.flush()
        write = recorder.store.write

        def unavailable(*args):
            raise sqlite3.OperationalError("fixture storage interruption")

        recorder.store.write = unavailable
        recorder.emit(ActivityUpdate(key="lost", kind="output", text="not durable"))
        await recorder.flush()
        assert recorder.lost
        recorder.store.write = write
        await recorder.close()

    asyncio.run(exercise())
    page = RunActivity(execution.workspace).read("harbor", run.id)
    assert page.omitted or any(e.omitted for e in page.items)
    assert all("\x1b" not in e.text for e in page.items)


def test_stream_burst_preserves_complete_reply_and_interleaved_tools(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {})
    store = RunActivity(execution.workspace)
    store.write("harbor", run.id, [ActivityUpdate(key="reply", kind="agent", text="obsolete")])
    reply = "TEAL, exact candidate " + "1234567890" * 40 + "; delivered."

    async def exercise():
        recorder = ActivityRecorder(execution.workspace, "harbor", run.id)
        for index, character in enumerate(reply):
            recorder.emit(
                ActivityUpdate(key="reply", kind="agent", text=character, append=index > 0)
            )
            if index == 20:
                recorder.emit(ActivityUpdate(key="tool", kind="tool", text="Reading task"))
            if index == 30:
                recorder.emit(ActivityUpdate(key="tool", kind="tool", text="Task read"))
        assert len(recorder.pending) == 2 and not recorder.lost
        await recorder.close()
        recorder.emit(ActivityUpdate(key="late", kind="agent", text="ignored"))

    asyncio.run(exercise())
    page = store.read("harbor", run.id)
    assert [(entry.key, entry.text) for entry in page.items] == [
        ("reply", reply),
        ("tool", "Task read"),
    ]
    assert not page.omitted and not any(entry.omitted for entry in page.items)


def test_stream_bounds_retain_opening_and_tail_and_reset_before_appending(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {})

    async def exercise():
        recorder = ActivityRecorder(execution.workspace, "harbor", run.id)
        recorder.emit(ActivityUpdate(key="reply", kind="agent", text="discard me"))
        recorder.emit(ActivityUpdate(key="reply", kind="agent", text="Opening. "))
        for _ in range(2000):
            recorder.emit(ActivityUpdate(key="reply", kind="agent", text="middle ", append=True))
        recorder.emit(ActivityUpdate(key="reply", kind="agent", text="The end.", append=True))
        assert len(recorder.pending) == 1 and len(recorder.pending[0].text) <= MAX_TEXT
        await recorder.close()

    asyncio.run(exercise())
    entry = RunActivity(execution.workspace).read("harbor", run.id).items[0]
    assert entry.text.startswith("Opening. ") and entry.text.endswith("The end.")
    assert "discard me" not in entry.text
    assert entry.omitted and "middle omitted" in entry.text


def test_concurrent_flushes_preserve_append_order(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {})
    entered, release = threading.Event(), threading.Event()
    store = RunActivity(execution.workspace)
    write = store.write

    def delayed_write(project, attempt, updates):
        if updates[0].text == "Opening":
            entered.set()
            assert release.wait(timeout=3)
        write(project, attempt, updates)

    store.write = delayed_write

    async def exercise():
        recorder = ActivityRecorder(execution.workspace, "harbor", run.id, store=store)
        recorder.emit(ActivityUpdate(key="reply", kind="agent", text="Opening"))
        first = asyncio.create_task(recorder.flush())
        assert await asyncio.to_thread(entered.wait, 3)
        recorder.emit(ActivityUpdate(key="reply", kind="agent", text=" tail", append=True))
        second = asyncio.create_task(recorder.flush())
        try:
            await asyncio.sleep(0.02)
            assert not second.done()
        finally:
            release.set()
        await asyncio.gather(first, second)
        await recorder.close()

    asyncio.run(exercise())
    assert store.read("harbor", run.id).items[0].text == "Opening tail"


def test_shared_state_matches_question_attention_and_static_pause(tmp_path):
    execution = fixture(tmp_path)
    run, item = question(execution)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=BASE)
    board = BrowserReads(execution.workspace).board("harbor")
    state = next(t.state for t in board.tasks if t.id == "task-0")
    attention = attention_page(execution.workspace, "harbor", "action")
    assert attention.items[0].state == state
    assert state.tone == "waiting" and item.id in state.href
    notices = attention_notices(execution.workspace)
    assert notices[0].href == state.href
    second = Questions(execution.workspace).ask(
        "harbor",
        QuestionCreate(
            task_id="task-0",
            question="Another choice?",
            context="A separate consequence",
            recommendation="Keep it small",
        ),
    )
    attention = attention_page(execution.workspace, "harbor", "action")
    assert all(item.id in item.state.href for item in attention.items if item.kind == "question")
    assert any(item.id == second.id for item in attention.items)
    execution.queue(
        "harbor", QueueEdit(expected_revision=execution.settings("harbor").revision, enabled=False)
    )
    # Another queued task is static, never an animated executor.
    assert all(
        t.state.tone != "active" for t in BrowserReads(execution.workspace).board("harbor").tasks
    )
