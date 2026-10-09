"""Native subprocess, settings, public output and cancellation regressions; no models."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowfield.adapters.codex_agent import CodexAgent
from flowfield.adapters.json_rpc import MAX_FRAME, MAX_INPUT, JsonRpc, NativeError
from flowfield.agent_models import AgentChoice
from flowfield.errors import ApplicationError

FAKE = Path(__file__).with_name("fake_native_codex.py")
CHOICE = AgentChoice(model="test-model", effort="low", mode="read-only", fast=False)


async def start(tmp_path, events, *flags, servers=None, resume=None):
    client = CodexAgent(tmp_path, tmp_path, {"CODEX_PATH": sys.executable, "HOME": str(tmp_path)})
    client.command = [sys.executable, str(FAKE), *flags]
    client.on_activity = events.append
    await client.start(servers or [], resume=resume, persistent=True)
    await client.configure(CHOICE)
    return client


def test_public_activity_excludes_private_reasoning_inputs_and_wrong_sessions(tmp_path):
    async def exercise():
        events = []
        agent = await start(tmp_path, events)
        try:
            assert await agent.prompt('{"mode":"normal"}', None) == {"status": "completed"}
            public = json.dumps([value.model_dump() for value in events])
            assert "finished" in public
            assert "PRIVATE" not in public and "WRONG SESSION" not in public
            assert any(value.context for value in events)
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["disconnect", "malformed", "oversized"])
def test_transport_failure_never_confirms_native_cleanup_or_replays(tmp_path, mode):
    async def exercise():
        agent = await start(tmp_path, [])
        with pytest.raises((NativeError, TimeoutError)):
            async with asyncio.timeout(3):
                await agent.prompt(json.dumps({"mode": mode}), None)
        assert not await agent.stop()
        assert agent.process.returncode is not None

    asyncio.run(exercise())


@pytest.mark.parametrize("flags", [(), ("cleanup-uncertain",)])
def test_stop_waiting_turn_is_idempotent_and_keeps_uncertainty(tmp_path, flags):
    async def exercise():
        entered = asyncio.Event()
        agent = await start(tmp_path, [], *flags)
        agent.on_activity = lambda value: entered.set() if "started" in value.text else None
        prompt = asyncio.create_task(agent.prompt('{"mode":"wait"}', None))
        await asyncio.wait_for(entered.wait(), 3)
        result = await asyncio.gather(agent.stop(), agent.stop())
        assert result == [not flags, not flags]
        await asyncio.gather(prompt, return_exceptions=True)
        assert agent.process.returncode is not None
        assert await agent.prompt("late", None) == {"status": "stopped"}

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field,value",
    [("model", "unoffered"), ("effort", "invented"), ("mode", "invented"), ("fast", True)],
)
def test_unavailable_choices_are_rejected_before_any_prompt(tmp_path, field, value):
    async def exercise():
        agent = CodexAgent(tmp_path, tmp_path, {"CODEX_PATH": sys.executable})
        agent.command = [sys.executable, str(FAKE)]
        await agent.start([])
        try:
            with pytest.raises(ApplicationError):
                await agent.configure(CHOICE.model_copy(update={field: value}))
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


def test_frame_bounds_allow_images_only_in_prompt_operations():
    class Writer:
        content = b""

        def write(self, content):
            self.content = content

        async def drain(self):
            pass

    async def reject(method, params):
        return {}

    async def exercise():
        writer = Writer()
        peer = JsonRpc(lambda *_: None, reject)
        peer.owner = SimpleNamespace(process=SimpleNamespace(stdin=writer))
        await peer.send({"method": "turn/start", "params": {"input": "x" * MAX_FRAME}})
        assert len(writer.content) > MAX_FRAME
        with pytest.raises(NativeError):
            await peer.send({"method": "initialize", "params": {"input": "x" * MAX_FRAME}})
        with pytest.raises(NativeError):
            await peer.send({"method": "prompt", "params": {"input": "x" * MAX_INPUT}})

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "flags,confirmed",
    [(("native-background",), True), (("native-background", "terminal-refused"), False)],
)
def test_stop_native_background_uses_protocol_and_keeps_refusal(tmp_path, flags, confirmed):
    from flowfield.adapters.local_process import LocalProcess

    async def exercise():
        tool = await LocalProcess.start(
            [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
            cwd=tmp_path,
            env={},
        )
        assert await tool.process.stdout.readline() == b"ready\n"
        agent = CodexAgent(
            tmp_path,
            tmp_path,
            {"CODEX_PATH": sys.executable, "FLOWFIELD_TEST_TOOL_PID": str(tool.process.pid)},
        )
        agent.command = [sys.executable, str(FAKE), *flags]
        try:
            await agent.start([])
            await agent.configure(CHOICE)
            assert await agent.stop() is confirmed
            if confirmed:
                await asyncio.wait_for(tool.process.wait(), 3)
            else:
                assert tool.process.returncode is None
        finally:
            await agent.close()
            await tool.close(timeout=0.2)

    asyncio.run(exercise())


def test_native_offered_decisions_cannot_grant_persistent_or_unoffered_permission(tmp_path):
    async def exercise():
        agent = await start(tmp_path, [])
        agent._completion = asyncio.get_running_loop().create_future()
        agent.turns[agent.session_id] = "owned-turn"
        offered = []

        async def choose(request):
            offered.append(request.options)
            return "allow"

        agent._permission = choose
        try:
            result = await agent._request(
                "item/commandExecution/requestApproval",
                {
                    "threadId": agent.session_id,
                    "turnId": "owned-turn",
                    "itemId": "owned-tool",
                    "availableDecisions": ["acceptForSession", "decline", "cancel"],
                },
            )
            assert offered == [(("deny", "Deny", "reject_once"),)]
            assert result == {"decision": "cancel"}
            result = await agent._request(
                "item/permissions/requestApproval",
                {
                    "threadId": agent.session_id,
                    "turnId": "owned-turn",
                    "permissions": {"network": True},
                },
            )
            assert result == {"permissions": {}, "scope": "turn"}
        finally:
            agent._completion = None
            agent.turns.clear()
            assert await agent.stop()

    asyncio.run(exercise())
