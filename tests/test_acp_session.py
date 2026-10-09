"""Shared ACP lifecycle against a real deterministic subprocess, with no credentials."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from acp.schema import AvailableCommandsUpdate

from flowfield.adapters.acp_session import AcpSession, ShutdownTimeouts
from flowfield.adapters.local_process import LocalProcess

FAKE = Path(__file__).with_name("fake_acp.py")


@pytest.mark.parametrize("flags", [(), ("early-commands",)])
def test_commands_arrive_before_or_after_session_and_update_while_idle(tmp_path, flags):
    async def exercise():
        client = await start(tmp_path, [], *flags)
        try:
            await asyncio.wait_for(client.commands_received.wait(), 2)
            assert "compact" in {item.name for item in client.commands}
            update = AvailableCommandsUpdate.model_validate(
                {
                    "sessionUpdate": "available_commands_update",
                    "availableCommands": [{"name": "renamed", "description": "New command"}],
                }
            )
            await client.session_update("wrong-session", update)
            assert "compact" in {item.name for item in client.commands}
            await client.session_update(client.session_id, update)
            assert [item.name for item in client.commands] == ["renamed"]
        finally:
            await client.close()

    asyncio.run(exercise())


def test_startup_metadata_is_frozen_before_asynchronous_process_start(tmp_path, monkeypatch):
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        original = LocalProcess.start

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(LocalProcess, "start", delayed)
        metadata = {"nativeFixture": {"model": "original-model"}}
        client = AcpSession(lambda _: None, request_timeout=2)
        startup = asyncio.create_task(
            client.start(
                [sys.executable, str(FAKE)],
                cwd=tmp_path,
                env={"FLOWFIELD_TEST_META": json.dumps(metadata)},
                mcp_servers=[],
                session_metadata=metadata,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            metadata["nativeFixture"]["model"] = "changed-model"
            release.set()
            await startup
            assert client.session_id == "test-session"
        finally:
            release.set()
            await asyncio.gather(startup, return_exceptions=True)
            assert (await client.close()).process_group_exited

    asyncio.run(exercise())


async def start(tmp_path, events, *flags, permission=None, load=None, resume=None):
    client = AcpSession(events.append, on_permission=permission, request_timeout=2)
    await client.start(
        [sys.executable, str(FAKE), *flags],
        cwd=tmp_path,
        env={},
        mcp_servers=[],
        load_session_id=load,
        resume_session_id=resume,
    )
    return client


@pytest.mark.parametrize("binding", ["new", "load", "resume"])
def test_adapter_metadata_reaches_each_session_binding_without_changing_transport(
    tmp_path, binding
):
    async def exercise():
        metadata = {"nativeFixture": {"model": "explicit-model", "persist": False}}
        client = AcpSession(lambda _: None, request_timeout=2)
        try:
            await client.start(
                [sys.executable, str(FAKE)],
                cwd=tmp_path,
                env={"FLOWFIELD_TEST_META": json.dumps(metadata)},
                mcp_servers=[],
                session_metadata=metadata,
                load_session_id="test-session" if binding == "load" else None,
                resume_session_id="test-session" if binding == "resume" else None,
            )
            assert client.session_id == "test-session"
            assert await client.prompt('{"mode":"normal"}') == "end_turn"
        finally:
            assert (await client.close()).process_group_exited

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["wait", "permission"])
def test_elapsed_time_does_not_end_a_turn_or_permission_and_stop_still_works(
    tmp_path, monkeypatch, mode
):
    async def exercise():
        entered = asyncio.Event()
        permission_cancelled = asyncio.Event()

        async def permission(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                permission_cancelled.set()

        events = []
        client = await start(tmp_path, events, "close-session", permission=permission)
        prompt = asyncio.create_task(client.prompt(json.dumps({"mode": mode})))
        try:
            async with asyncio.timeout(3):
                while not events and not entered.is_set():
                    await asyncio.sleep(0.01)
            # Advance beyond the former 15-minute adapter and one-hour session limits
            # without a live model or a long-running test.
            loop = asyncio.get_running_loop()
            clock = loop.time
            monkeypatch.setattr(loop, "time", lambda: clock() + 7200)
            await asyncio.sleep(0.02)
            assert not prompt.done()
            assert client.state == "running"
            assert not permission_cancelled.is_set()
            receipt = await client.close()
            assert receipt.turn_finished and receipt.process_group_exited
            assert await prompt == "cancelled"
            if mode == "permission":
                assert permission_cancelled.is_set()
        finally:
            await client.close()
            await asyncio.gather(prompt, return_exceptions=True)

    asyncio.run(exercise())


def test_resume_negotiates_capability_and_does_not_replay_history(tmp_path):
    async def exercise():
        events = []
        client = await start(tmp_path, events, resume="test-session")
        try:
            assert not events
            assert client.session_id == "test-session"
            assert await client.prompt('{"mode":"normal"}') == "end_turn"
        finally:
            assert (await client.close()).process_group_exited
        with pytest.raises(RuntimeError, match="does not support resuming"):
            await start(tmp_path, [], "no-resume", resume="test-session")
        with pytest.raises(ValueError, match="load|resume"):
            await start(tmp_path, [], load="test-session", resume="test-session")

    asyncio.run(exercise())


def test_stream_model_scope_and_no_private_reasoning(tmp_path, caplog):
    async def exercise():
        events = []
        client = await start(tmp_path, events)
        try:
            with pytest.raises(ValueError, match="unavailable"):
                await client.select("model", "absent")
            await client.select("model", "second")
            assert client.config[0]["currentValue"] == "second"
            assert await client.prompt('{"mode":"stderr"}') == "end_turn"
            assert [event.kind for event in events] == ["tool", "usage", "text"]
            assert events[1].data == {"used": 100, "size": 1000}
            assert "PRIVATE" not in str(events) and "WRONG" not in str(events)
            assert client.state == "ready"
        finally:
            receipt = await client.close()
        assert receipt.turn_finished and receipt.process_group_exited
        assert await client.close() == receipt

    asyncio.run(exercise())
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize(
    "choice,expected", [("allow", "selected"), ("invented", "cancelled"), (None, "cancelled")]
)
def test_permissions_only_accept_offered_options(tmp_path, choice, expected):
    async def exercise():
        async def permission(request):
            assert request.tool_id == "tool-1"
            return choice

        events = []
        client = await start(tmp_path, events, permission=permission)
        try:
            await client.prompt('{"mode":"permission"}')
            response = json.loads(events[0].data["text"])
            assert response["outcome"]["outcome"] == expected
        finally:
            await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("title", [None, "Updated permission title"])
def test_partial_permission_reuses_matching_tool_title_and_public_details(title):
    from acp.schema import PermissionOption, ToolCallStart, ToolCallUpdate

    async def exercise():
        requests = []

        async def permission(request):
            requests.append(request)
            return "allow"

        client = AcpSession(lambda event: None, on_permission=permission)
        client.session_id = "session"
        client.state = "running"
        await client.session_update(
            "session",
            ToolCallStart.model_validate(
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "read-context",
                    "title": "Read task context",
                    "kind": "read",
                    "status": "in_progress",
                    "content": [
                        {"type": "content", "content": {"type": "text", "text": "Task PKT-1"}}
                    ],
                    "rawInput": {"secret": "PRIVATE"},
                }
            ),
        )
        options = [PermissionOption(option_id="allow", name="Allow", kind="allow_once")]
        for identity in ("read-context", "unrelated"):
            await client.request_permission(
                "session", ToolCallUpdate(tool_call_id=identity, title=title), options
            )
        assert requests[0].title == (title or "Read task context")
        assert requests[0].details == "Task PKT-1"
        assert requests[1].title == (title or "Tool permission")
        assert requests[1].details == ""
        assert "PRIVATE" not in str(requests)

    asyncio.run(exercise())


def test_stop_cancels_pending_permission_and_blocks_new_work(tmp_path):
    async def exercise():
        requested = asyncio.Event()

        async def permission(request):
            requested.set()
            await asyncio.Event().wait()

        client = await start(tmp_path, [], "close-session", permission=permission)
        prompt = asyncio.create_task(client.prompt('{"mode":"permission"}'))
        await asyncio.wait_for(requested.wait(), 3)
        with pytest.raises(RuntimeError, match="not ready"):
            await client.prompt("second turn")
        with pytest.raises(RuntimeError, match="not ready"):
            await client.select("model", "second")
        receipt = await client.close()
        assert receipt.turn_finished and receipt.process_group_exited
        assert receipt.session_closed is True
        assert await prompt == "cancelled"
        with pytest.raises(RuntimeError, match="not ready"):
            await client.prompt("after stop")

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["disconnect", "oversized", "malformed"])
def test_broken_transport_never_replays_or_reuses_session(tmp_path, mode):
    async def exercise():
        client = await start(tmp_path, [])
        with pytest.raises((ConnectionError, RuntimeError)):
            await client.prompt(json.dumps({"mode": mode}))
        assert client.state == "interrupted"
        assert client.process.returncode is not None
        with pytest.raises(RuntimeError, match="not ready"):
            await client.prompt("retry")

    asyncio.run(exercise())


def test_unacknowledged_stop_is_uncertain_even_after_process_exit(tmp_path):
    async def exercise():
        client = await start(tmp_path, [])
        prompt = asyncio.create_task(client.prompt('{"mode":"ignore_cancel"}'))
        await asyncio.sleep(0.1)
        receipt = await client.close(
            timeouts=ShutdownTimeouts(cancellation=0.15, session=0.15, process_exit=5)
        )
        assert not receipt.turn_finished
        assert receipt.process_group_exited
        assert client.state == "interrupted"
        with pytest.raises((ConnectionError, asyncio.CancelledError)):
            await prompt

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "flag,load,message",
    [
        ("bad-version", None, "protocol version"),
        ("no-load", "old", "loading sessions"),
        ("", "missing", "Session missing"),
    ],
)
def test_capability_and_missing_session_errors_are_explicit(tmp_path, flag, load, message):
    async def exercise():
        with pytest.raises(Exception, match=message):
            await start(tmp_path, [], flag, load=load)

    asyncio.run(exercise())


def test_model_fallback_closes_session(tmp_path):
    async def exercise():
        client = await start(tmp_path, [], "fallback")
        with pytest.raises(RuntimeError, match="did not apply"):
            await client.select("model", "second")
        assert client.state != "ready" and client.process.returncode is not None

    asyncio.run(exercise())


def test_event_delivery_failure_interrupts_instead_of_claiming_success(tmp_path):
    async def exercise():
        client = await start(tmp_path, [])

        def fail(event):
            raise RuntimeError("Storage unavailable")

        client.on_event = fail
        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await client.prompt("{}")
        assert client.state == "interrupted"
        assert client.process.returncode is not None

    asyncio.run(exercise())


def test_load_is_explicit_and_does_not_resend_previous_work(tmp_path):
    async def exercise():
        events = []
        client = await start(tmp_path, events, load="test-session")
        assert client.state == "ready" and events == []
        await client.prompt("{}")
        assert [event.data["text"] for event in events if event.kind == "text"] == ["finished"]
        await client.close()

    asyncio.run(exercise())


def test_stop_during_startup_and_cancelled_stop_caller(tmp_path):
    async def exercise():
        client = AcpSession(lambda event: None, request_timeout=2)
        startup = asyncio.create_task(
            client.start(
                [sys.executable, str(FAKE), "slow-start"],
                cwd=tmp_path,
                env={},
                mcp_servers=[],
            )
        )
        async with asyncio.timeout(2):
            while client.process is None:
                await asyncio.sleep(0.001)
        stop = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
        receipt = await client.close()
        assert receipt.process_group_exited
        with pytest.raises((ConnectionError, RuntimeError)):
            await startup
        assert client.state != "ready" and client.process.returncode is not None

    asyncio.run(exercise())


def test_negotiated_session_close_and_eof_precede_process_signals(tmp_path):
    async def exercise():
        client = await start(tmp_path, [], "close-session", "slow-exit")
        await client.prompt("{}")
        receipt = await client.close()
        assert receipt.session_closed is True
        assert receipt.process_exited_gracefully
        assert receipt.process_group_exited and receipt.turn_finished
        assert client.process.returncode == 0 and client.state == "closed"

    asyncio.run(exercise())


@pytest.mark.parametrize("flag", ["close-failure", "close-hang"])
def test_failed_native_session_close_retains_uncertainty(tmp_path, flag):
    async def exercise():
        client = await start(tmp_path, [], "close-session", flag)
        receipt = await client.close(
            timeouts=ShutdownTimeouts(cancellation=0.15, session=0.15, process_exit=5)
        )
        assert receipt.session_closed is False
        assert receipt.process_group_exited
        assert client.state == "interrupted"

    asyncio.run(exercise())


def test_closing_one_session_does_not_interrupt_another(tmp_path):
    async def exercise():
        first_events, second_events = [], []
        first = await start(tmp_path, first_events, "close-session")
        second = await start(tmp_path, second_events, "close-session")
        first_turn = asyncio.create_task(first.prompt('{"mode":"wait"}'))
        second_turn = asyncio.create_task(second.prompt('{"mode":"wait"}'))
        try:
            async with asyncio.timeout(3):
                while not first_events or not second_events:
                    await asyncio.sleep(0.01)
            receipt = await first.close()
            assert receipt.session_closed and receipt.process_group_exited
            assert await first_turn == "cancelled"
            assert not second_turn.done()
            assert second.state == "running" and second.process.returncode is None
        finally:
            await first.close()
            await second.close()
            await asyncio.gather(first_turn, second_turn, return_exceptions=True)

    asyncio.run(exercise())


def test_partial_tool_updates_keep_public_facts_and_bound_cache():
    from acp.schema import ToolCallProgress, ToolCallStart

    from flowfield.adapters.codex_agent import codex_activity_details

    async def exercise():
        events = []
        client = AcpSession(events.append, activity_projection=codex_activity_details)
        client.session_id = "session"
        client.state = "running"
        await client.session_update(
            "session",
            ToolCallStart.model_validate(
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "check",
                    "title": "Run tests",
                    "kind": "execute",
                    "status": "in_progress",
                    "locations": [{"path": "/project/test.py"}],
                    "rawInput": {"command": "pnpm test", "cwd": "/project", "secret": "PRIVATE"},
                    "rawOutput": "PRIVATE OUTPUT",
                }
            ),
        )
        await client.session_update(
            "session",
            ToolCallProgress.model_validate(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "check",
                    "status": "completed",
                }
            ),
        )
        final = events[-1].data
        assert final["title"] == "Run tests" and final["status"] == "completed"
        assert final["kind"] == "execute"
        assert "pnpm test" in final["details"] and "/project/test.py" in final["details"]
        assert "PRIVATE" not in str(events)
        assert events[0].data["status"] == "in_progress"
        await client.session_update(
            "session",
            ToolCallProgress.model_validate(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "check",
                    "status": "failed",
                    "rawOutput": {
                        "error": {"message": "Connection closed", "secret": "PRIVATE"},
                        "result": {
                            "structuredContent": {
                                "error": {
                                    "code": "tool_busy",
                                    "message": "Wait for the current operation.",
                                },
                                "private": "PRIVATE",
                            },
                        },
                    },
                }
            ),
        )
        assert "Connection closed" in events[-1].data["details"]
        assert "Wait for the current operation." in events[-1].data["details"]
        assert "PRIVATE" not in str(events)
        for index in range(110):
            await client.session_update(
                "session",
                ToolCallStart.model_validate(
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": str(index),
                        "title": "x" * 10000,
                        "kind": "read",
                        "status": "completed",
                    }
                ),
            )
        assert len(client._tool_activity) == 100
        assert all(len(item["title"]) <= 4000 for item in client._tool_activity.values())

    asyncio.run(exercise())
