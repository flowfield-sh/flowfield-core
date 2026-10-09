"""Real callback lifetime and durable decisions; no live models or permissive worker launch."""

import asyncio
import json
from contextlib import suppress

import pytest
from test_execution import BASE, fixture
from test_native_adapters import start

from flowfield.adapters.agent_permissions import permission_handler
from flowfield.attention import attention_page
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.permission_models import PermissionAnswer, PermissionOption
from flowfield.permissions import Permissions

OPTIONS = [
    PermissionOption(id="yes", label="Allow once", kind="allow_once"),
    PermissionOption(id="no", label="Reject once", kind="reject_once"),
]


def setup(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    return execution, run, Permissions(execution.workspace)


async def pending(owner):
    async with asyncio.timeout(4):
        while not (items := owner.page("harbor").pending):
            await asyncio.sleep(0.01)
        return items[0]


@pytest.mark.parametrize("option", ["yes", "no"])
def test_answer_is_offered_exactly_once_and_persisted(tmp_path, option):
    async def exercise():
        execution, run, owner = setup(tmp_path)
        async with owner.turn(
            "harbor", "worker", session_id="native", turn_id="one", run_id=run.id
        ) as turn:
            task = asyncio.create_task(turn.request("tool", "Run a check", OPTIONS))
            record = await pending(owner)
            attention = attention_page(execution.workspace, "harbor", "action")
            assert attention.items[0].kind == "permission"
            with pytest.raises(ApplicationError, match="offered"):
                owner.answer(
                    "harbor", record.id, PermissionAnswer(expected_revision=1, option_id="invented")
                )
            with pytest.raises(ApplicationError):
                owner.answer(
                    "other-project",
                    record.id,
                    PermissionAnswer(expected_revision=1, option_id=option),
                )
            request = PermissionAnswer(expected_revision=1, option_id=option)
            saved = owner.answer("harbor", record.id, request)
            assert saved.answer == option
            assert owner.answer("harbor", record.id, request) == saved
            with pytest.raises(ApplicationError):
                owner.answer(
                    "harbor",
                    record.id,
                    PermissionAnswer(
                        expected_revision=1, option_id="no" if option == "yes" else "yes"
                    ),
                )
            assert await task == option
            assert not owner.page("harbor").pending
            assert owner.page("harbor").items[0].released_at
            assert execution.get("harbor", run.id).status == "running"
        recovered = Permissions(execution.workspace)
        recovered.restart()
        assert recovered.page("harbor").items[0].answer == option
        assert recovered.page("harbor").items[0].status == "answered"

    asyncio.run(exercise())


def test_expiry_cancel_and_answer_racing_disconnect(tmp_path):
    async def exercise():
        _, run, owner = setup(tmp_path)
        async with owner.turn(
            "harbor", "worker", session_id="native", turn_id="one", run_id=run.id
        ) as turn:
            assert await turn.request("tool", "Expired", OPTIONS, timeout=0.01) is None
            expired = owner.page("harbor").items[0]
            assert expired.status == "expired"
            with pytest.raises(ApplicationError):
                owner.answer(
                    "harbor", expired.id, PermissionAnswer(expected_revision=1, option_id="yes")
                )
            task = asyncio.create_task(turn.request("tool", "Racing disconnect", OPTIONS))
            record = await pending(owner)
            owner.answer(
                "harbor", record.id, PermissionAnswer(expected_revision=1, option_id="yes")
            )
            owner.close()  # no event-loop yield: answer saved, callback has not consumed it
            with suppress(asyncio.CancelledError, ApplicationError):
                await task
            closed = owner.page("harbor").items[0]
            assert (
                closed.status == "cancelled" and closed.answer == "yes" and not closed.released_at
            )
            with pytest.raises(ApplicationError):
                owner.answer(
                    "harbor", record.id, PermissionAnswer(expected_revision=1, option_id="yes")
                )
        assert not owner.turns

    asyncio.run(exercise())


def test_stop_rejects_answer_and_new_turn_does_not_replay(tmp_path):
    async def exercise():
        execution, run, owner = setup(tmp_path)
        async with owner.turn(
            "harbor", "worker", session_id="native", turn_id="one", run_id=run.id
        ) as turn:
            task = asyncio.create_task(turn.request("tool", "Stop", OPTIONS))
            record = await pending(owner)
            current = execution.get("harbor", run.id)
            execution.stop_requested(
                "harbor", run.id, RunAction(expected_revision=current.revision)
            )
            with pytest.raises(ApplicationError, match="no longer running"):
                owner.answer(
                    "harbor", record.id, PermissionAnswer(expected_revision=1, option_id="yes")
                )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert owner.page("harbor").items[0].status == "cancelled"
        from test_coordinator_generations import start_coordinator

        _, coordinator, generation = start_coordinator(execution.workspace)
        async with owner.turn(
            "harbor",
            "coordinator",
            session_id="native",
            turn_id=coordinator.id,
            conversation_id=coordinator.conversation_id,
            generation_id=generation.id,
        ) as turn:
            task = asyncio.create_task(turn.request("tool", "Same tool, new turn", OPTIONS))
            new = await pending(owner)
            assert new.id != record.id and new.binding != record.binding
            owner.answer("harbor", new.id, PermissionAnswer(expected_revision=1, option_id="no"))
            assert await task == "no"

    asyncio.run(exercise())


@pytest.mark.parametrize("status", ["pending", "answered"])
def test_restart_cancels_orphaned_saved_answer(tmp_path, status):
    async def exercise():
        execution, run, owner = setup(tmp_path)
        async with owner.turn(
            "harbor", "worker", session_id="native", turn_id="one", run_id=run.id
        ) as turn:
            task = asyncio.create_task(turn.request("tool", "Check", OPTIONS))
            record = await pending(owner)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        # Crash fixture: transaction committed an answer, process died before delivering it.
        record.status, record.answer = status, "yes" if status == "answered" else None
        with execution.workspace.connection(write=True) as db:
            owner._save(db, record)
        replacement = Permissions(execution.workspace)
        replacement.restart()
        assert replacement.page("harbor").items[0].status == "cancelled"
        assert replacement.page("harbor").items[0].answer == record.answer
        with pytest.raises(ApplicationError):
            replacement.answer(
                "harbor", record.id, PermissionAnswer(expected_revision=1, option_id="yes")
            )

    asyncio.run(exercise())


@pytest.mark.parametrize("action", ["allow", "deny", "close"])
def test_real_native_permission_roundtrip_and_disconnect(tmp_path, action):
    async def exercise():
        _, run, owner = setup(tmp_path)
        events = []
        client = await start(tmp_path, events)
        try:
            async with owner.turn(
                "harbor", "worker", session_id=client.session_id, turn_id="one", run_id=run.id
            ) as turn:
                task = asyncio.create_task(
                    client.prompt('{"mode":"permission"}', permission_handler(turn))
                )
                record = await pending(owner)
                if action == "close":
                    await client.close()
                    assert (await task)["status"] == "stopped"
                    assert owner.page("harbor").items[0].status == "cancelled"
                else:
                    owner.answer(
                        "harbor", record.id, PermissionAnswer(expected_revision=1, option_id=action)
                    )
                    assert (await task)["status"] == "completed"
                    assert json.loads(
                        next(value.text for value in events if value.kind == "agent")
                    )["outcome"] == {
                        "outcome": "selected",
                        "optionId": action,
                    }
                    assert owner.page("harbor").items[0].answer == action
        finally:
            await client.close()
        assert not owner.turns

    asyncio.run(exercise())


def test_parallel_requests_and_bounded_history_keep_pending_visible(tmp_path):
    async def exercise():
        execution = fixture(tmp_path, count=2, cap=2)
        first = execution.claim("harbor", BASE, {BASE: set()})
        second = execution.claim("harbor", BASE, {BASE: set()})
        execution.started("harbor", first.id)
        execution.started("harbor", second.id)
        owner = Permissions(execution.workspace)
        async with owner.turn(
            "harbor", "worker", session_id="one", turn_id="a", run_id=first.id
        ) as one:
            async with owner.turn(
                "harbor", "worker", session_id="two", turn_id="b", run_id=second.id
            ) as two:
                waiting = asyncio.create_task(one.request("same-tool", "First worker", OPTIONS))
                original = await pending(owner)
                for _ in range(3):
                    replying = asyncio.create_task(
                        two.request("same-tool", "Second worker", OPTIONS)
                    )
                    async with asyncio.timeout(2):
                        while len(owner.page("harbor").pending) != 2:
                            await asyncio.sleep(0.01)
                    record = owner.page("harbor").pending[1]
                    owner.answer(
                        "harbor", record.id, PermissionAnswer(expected_revision=1, option_id="no")
                    )
                    assert await replying == "no"
                page = owner.page("harbor", limit=1)
                assert page.pending == [original] and len(page.items) == 1 and page.next_before
                older = owner.page("harbor", limit=1, before=page.next_before)
                assert older.pending == [original] and older.items[0].id != page.items[0].id
                owner.close_run("harbor", second.id)
                assert not waiting.done()
                owner.answer(
                    "harbor", original.id, PermissionAnswer(expected_revision=1, option_id="yes")
                )
                assert await waiting == "yes"

    asyncio.run(exercise())


def test_transport_loss_cancels_durable_pending_request(tmp_path):
    async def exercise():
        _, run, owner = setup(tmp_path)
        client = await start(tmp_path, [])
        try:
            async with owner.turn(
                "harbor", "worker", session_id=client.session_id, turn_id="lost", run_id=run.id
            ) as turn:
                client.on_permission = permission_handler(turn)
                prompt = asyncio.create_task(
                    client.prompt('{"mode":"permission_disconnect"}', permission_handler(turn))
                )
                record = await pending(owner)
                with pytest.raises((ConnectionError, RuntimeError)):
                    await prompt
                assert owner.page("harbor").items[0].status == "cancelled"
                with pytest.raises(ApplicationError):
                    owner.answer(
                        "harbor",
                        record.id,
                        PermissionAnswer(expected_revision=1, option_id="allow"),
                    )
        finally:
            await client.close()

    asyncio.run(exercise())
