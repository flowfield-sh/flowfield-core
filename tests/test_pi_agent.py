"""Pi's native boundary, including failed ownership and provider-qualified choices."""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from flowfield.adapters.agent_contract import McpServer
from flowfield.adapters.json_rpc import NativeError
from flowfield.adapters.pi_agent import PiAgent, model_id, model_options
from flowfield.agent_models import AgentChoice
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessRegistration


def fixture(tmp_path, scenario="complete"):
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    native = tmp_path / "pi"
    native.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(Path(__file__).with_name("fake_pi.py")))
        + ' "$@"\n'
    )
    native.chmod(0o755)
    registration = HarnessRegistration(
        harness="pi", executable=str(native), config_directory=str(config)
    )
    agent = PiAgent(
        tmp_path,
        tmp_path,
        {**os.environ, "PI_TEST_SCENARIO": scenario, "PI_TEST_LOG": str(tmp_path / "calls.jsonl")},
        registration=registration,
    )
    return agent, registration


def choice(**kwargs):
    return AgentChoice(
        harness="pi", model="first/shared-model", effort="low", mode="full-access", **kwargs
    )


def test_provider_model_separator_is_unambiguous():
    assert model_id({"provider": "a/b", "id": "c"}) == "a%2Fb/c"
    assert model_id({"provider": "a", "id": "b/c"}) == "a/b%2Fc"
    assert model_id({"provider": "a%2Fb", "id": "c"}) == "a%252Fb/c"


def test_model_provider_identity_and_native_levels(tmp_path, monkeypatch):
    _, registration = fixture(tmp_path)
    monkeypatch.setenv("PI_TEST_LOG", str(tmp_path / "calls.jsonl"))
    cleanups = []
    models = asyncio.run(
        model_options(tmp_path, registration=registration, on_cleanup=cleanups.append)
    )
    assert [model.id for model in models] == ["first/shared-model", "second/shared-model"]
    assert all(model.efforts == ["off", "low", "high"] for model in models)
    assert cleanups == [True, False, True]
    assert "prompt" not in [
        json.loads(line)["type"] for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]


@pytest.mark.parametrize("scenario", ["complete", "error", "disconnect", "queued"])
def test_turn_waits_for_settled_and_never_replays(tmp_path, scenario):
    async def exercise():
        agent, _ = fixture(tmp_path, scenario)
        updates = []
        agent.on_activity = updates.append
        try:
            await agent.start(
                [McpServer(name="flowfield_abc123", url="http://127.0.0.1:1/mcp", headers=[])],
                persistent=True,
            )
            assert await agent.configure(choice()) == choice()
            assert agent.input_tokens_available == 7000
            if scenario == "complete":
                assert await agent.prompt("hello", None) == {"status": "completed"}
                assert any(update.text == "Settled answer" for update in updates)
                assert any("Flowfield · Get task" in update.text for update in updates)
                assert any("Task missing" in update.text for update in updates)
                assert not any("private reasoning" in update.text for update in updates)
            else:
                with pytest.raises(NativeError):
                    await agent.prompt("hello", None)
        finally:
            assert await agent.stop() is (scenario != "disconnect")
        calls = [
            json.loads(line)["type"] for line in (tmp_path / "calls.jsonl").read_text().splitlines()
        ]
        assert calls.count("prompt") == 1
        if scenario != "disconnect":
            assert calls.index("clear_queue") < calls.index("abort") < calls.index("abort_bash")

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario", ["refused", "busy-stop", "missing-shutdown"])
def test_exit_alone_never_confirms_native_cleanup(tmp_path, scenario):
    async def exercise():
        agent, _ = fixture(tmp_path, scenario)
        await agent.start([])
        assert not await agent.stop()
        assert agent.process.returncode is not None
        assert not await agent.stop()

    asyncio.run(exercise())


def test_stop_interrupts_turn_and_rejects_future_prompts(tmp_path):
    async def exercise():
        agent, _ = fixture(tmp_path, "foreground")
        await agent.start([])
        await agent.configure(choice())
        turn = asyncio.create_task(agent.prompt("work", None))
        while agent._completion is None:
            await asyncio.sleep(0)
        assert await agent.stop()
        assert await turn == {"status": "stopped"}
        assert await agent.prompt("more", None) == {"status": "stopped"}

    asyncio.run(exercise())


def test_settings_and_resume_require_exact_native_confirmation(tmp_path):
    async def exercise():
        agent, _ = fixture(tmp_path, "wrong-session")
        try:
            with pytest.raises(ApplicationError, match="Nothing was replayed"):
                await agent.start([], resume="owned-session")
        finally:
            await agent.stop()
        agent, _ = fixture(tmp_path, "wrong-effort")
        try:
            await agent.start([])
            with pytest.raises(NativeError, match="apply"):
                await agent.configure(choice())
            with pytest.raises(NativeError, match="settings"):
                await agent.prompt("must not dispatch", None)
            assert await agent._request("extension_ui_request", {"method": "confirm"}) == {
                "cancelled": True
            }
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("server_name", ["flowfield_ab12", "flowfield_" + "a" * 32])
def test_native_tool_scope_and_text_image_attachments(tmp_path, monkeypatch, server_name):
    async def exercise():
        agent, _ = fixture(tmp_path)
        commands = []
        servers = []
        start = agent.rpc.start

        async def observed(command, cwd, env):
            commands.append(command)
            servers.extend(json.loads(env["FLOWFIELD_PI_SERVERS"]))
            return await start(command, cwd, env)

        monkeypatch.setattr(agent.rpc, "start", observed)
        try:
            await agent.start(
                [McpServer(name=server_name, url="http://127.0.0.1:1/mcp", headers=[])]
            )
            await agent.configure(choice())
            await agent.prompt(
                "read",
                None,
                attachments=[
                    {"name": "note.txt", "mime": "text/plain", "data": "dGV4dA=="},
                    {"name": "image.png", "mime": "image/png", "data": "aW1hZ2U="},
                ],
            )
            agent.model["input"] = ["text"]
            with pytest.raises(ApplicationError, match="images"):
                agent.validate_attachments([{"mime": "image/png"}])
        finally:
            assert await agent.stop()
        command = commands[0]
        assert "--no-extensions" in command and "--no-approve" in command
        assert len(servers) == 1 and servers[0]["config"]["url"] == "http://127.0.0.1:1/mcp"
        namespace = "mcp__" + servers[0]["name"] + "__"
        assert command[command.index("--tools") + 1].endswith(namespace + "*")
        assert len(namespace + "get_integration_settings") <= 64
        calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
        prompt = next(call for call in calls if call["type"] == "prompt")
        assert "text" in prompt["message"] and prompt["images"][0]["mimeType"] == "image/png"

    asyncio.run(exercise())
