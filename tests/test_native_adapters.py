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


@pytest.mark.parametrize("resume", [None, "test-session"])
@pytest.mark.parametrize(
    "requested,returned,accepted",
    [
        (True, "priority", True),
        (True, "fast", True),
        (True, "default", False),
        (False, "priority", False),
        (False, "default", True),
    ],
)
def test_native_fast_tier_confirmation_on_start_and_resume(
    tmp_path, resume, requested, returned, accepted
):
    from flowfield.adapters.codex_agent import catalog

    async def exercise():
        agent = CodexAgent(tmp_path, tmp_path, {"CODEX_PATH": sys.executable})
        agent.command = [sys.executable, str(FAKE)]
        await agent.start([], resume=resume)
        # Native model/list may advertise only the canonical service tier.
        agent.models[0]["serviceTiers"] = [{"id": "priority", "name": "Fast"}]
        assert catalog(agent)[0].fast
        call = agent.rpc.call

        async def response(method, params):
            if method in {"thread/start", "thread/resume"}:
                assert params["serviceTier"] == ("fast" if requested else "default")
            result = await call(method, params)
            if method in {"thread/start", "thread/resume"}:
                result["serviceTier"] = returned
            return result

        agent.rpc.call = response
        choice = CHOICE.model_copy(update={"fast": requested})
        try:
            if accepted:
                assert await agent.configure(choice) == choice
                assert await agent.prompt('{"mode":"normal"}', None) == {"status": "completed"}
            else:
                with pytest.raises(ApplicationError):
                    await agent.configure(choice)
                assert agent.choice is None
        finally:
            assert await agent.stop()

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


def test_public_tool_labels_preserve_names_errors_and_command_details(tmp_path):
    from flowfield.activity_text import preview
    from flowfield.adapters.codex_activity import describe

    item = {
        "type": "mcpToolCall",
        "server": "flowfield_" + "a" * 32,
        "tool": "get_task",
        "arguments": {"task_id": "fol-6"},
        "result": {
            "isError": True,
            "structuredContent": {"error": {"message": "Task not found in this project."}},
        },
    }
    title, details = describe(item)
    assert title == "Flowfield · Get task · fol-6"
    text = title + " · failed\n" + details
    summary = preview(text, "tool")
    assert "Task not found" in summary and "Arguments" not in summary
    assert '"task_id": "fol-6"' in details and "a" * 32 not in title
    item["result"] = {"structuredContent": None}
    item["error"] = {"message": "Connection lost"}
    assert "Connection lost" in describe(item)[1]
    command = "/bin/zsh -lc 'git status --short'"
    title, details = describe(
        {"type": "commandExecution", "command": command, "cwd": "/project", "exitCode": 0}
    )
    assert title == "git status --short" and command in details and "/project" in details
    assert (
        preview(title + " · completed\n" + details, "command")
        == "git status --short · completed\nExit code: 0"
    )
    assert (
        preview("commandExecution · completed\n" + command + "\n/project", "command")
        == "git status --short · completed"
    )
    assert (
        preview("Bash · completed\ncommand: git status\ncwd: /project", "command")
        == "git status · completed"
    )


@pytest.mark.parametrize(
    "selection,action",
    [("allow", "accept"), ("deny", "decline"), ("always", "cancel"), (None, "cancel")],
)
def test_scoped_mcp_approval_maps_only_one_time_decisions(tmp_path, selection, action):
    from flowfield.adapters.agent_contract import McpServer

    async def exercise():
        agent = await start(
            tmp_path,
            [],
            servers=[McpServer(name="flowfield_ab12", url="http://127.0.0.1:1/mcp", headers=[])],
        )
        agent._completion = asyncio.get_running_loop().create_future()
        agent.turns[agent.session_id] = "owned-turn"
        offered = []

        async def choose(request):
            offered.append(request)
            return selection

        agent._permission = choose
        request = {
            "threadId": agent.session_id,
            "turnId": "owned-turn",
            "serverName": "flowfield_ab12",
            "mode": "form",
            "message": 'Allow the flowfield_ab12 MCP server to run tool "submit_result"?',
            "requestedSchema": {"type": "object", "properties": {}},
            "_meta": {
                "codex_approval_kind": "mcp_tool_call",
                "persist": ["session", "always"],
                "tool_params": {"summary": "Done"},
            },
        }
        try:
            result = await agent._request("mcpServer/elicitation/request", request)
            assert result == {
                "action": action,
                "content": {} if action == "accept" else None,
                "_meta": None,
            }
            assert offered[0].title == "Flowfield · Submit result"
            assert [option[2] for option in offered[0].options] == ["allow_once", "reject_once"]
            assert '"summary": "Done"' in offered[0].details
            for update in (
                {"threadId": "other"},
                {"turnId": None},
                {"serverName": "ambient"},
                {"mode": "url"},
                {
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"secret": {"type": "string"}},
                    }
                },
                {"_meta": {"codex_approval_kind": "browser_auth"}},
            ):
                assert (
                    await agent._request("mcpServer/elicitation/request", {**request, **update})
                )["action"] == "cancel"
            assert len(offered) == 1

            async def stopped(_):
                agent.stopping = True
                return "allow"

            agent._permission = stopped
            assert (await agent._request("mcpServer/elicitation/request", request))[
                "action"
            ] == "cancel"
        finally:
            agent._completion = None
            agent.turns.clear()
            assert await agent.stop()

    asyncio.run(exercise())


def test_mcp_user_input_approval_requires_observed_scoped_call(tmp_path):
    from flowfield.adapters.agent_contract import McpServer

    async def exercise():
        agent = await start(
            tmp_path,
            [],
            servers=[McpServer(name="flowfield_ab12", url="http://127.0.0.1:1/mcp", headers=[])],
        )
        agent._completion = asyncio.get_running_loop().create_future()
        agent.turns[agent.session_id] = "owned-turn"
        agent._foreground_id = "owned-turn"
        seen = []

        async def choose(request):
            seen.append(request)
            return "allow"

        agent._permission = choose
        request = {
            "threadId": agent.session_id,
            "turnId": "owned-turn",
            "itemId": "call-1",
            "questions": [
                {
                    "id": "mcp_tool_call_approval_call-1",
                    "isOther": False,
                    "isSecret": False,
                    "options": [
                        {"label": "Allow"},
                        {"label": "Allow for this session"},
                        {"label": "Cancel"},
                    ],
                }
            ],
        }
        event = {
            "threadId": agent.session_id,
            "turnId": "owned-turn",
            "item": {
                "id": "call-1",
                "type": "mcpToolCall",
                "server": "flowfield_ab12",
                "tool": "submit_result",
                "arguments": {"summary": "Done"},
            },
        }
        try:
            assert await agent._request("item/tool/requestUserInput", request) == {"answers": {}}
            agent._event("item/started", event)
            assert await agent._request("item/tool/requestUserInput", request) == {
                "answers": {"mcp_tool_call_approval_call-1": {"answers": ["Allow"]}}
            }
            assert seen[0].title == "Flowfield · Submit result"
            assert len(seen[0].options) == 2
            # Ordinary model questions and unoffered grants cannot reuse approval state.
            assert await agent._request(
                "item/tool/requestUserInput",
                {**request, "questions": [{**request["questions"][0], "id": "ordinary-question"}]},
            ) == {"answers": {}}

            async def completed(_):
                agent._event("item/completed", event)
                return "allow"

            agent._permission = completed
            assert await agent._request("item/tool/requestUserInput", request) == {"answers": {}}
            assert len(seen) == 1
        finally:
            agent._completion = None
            agent.turns.clear()
            assert await agent.stop()

    asyncio.run(exercise())
